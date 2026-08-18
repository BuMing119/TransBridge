"""FR12 Story 01 集成验证 — 模拟 ChatWidget→Orchestrator→Controller 完整回调链路。

验证新旧并行路径：Controller 状态转换是否正确追踪实际对话流程。
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from transbridge.smart_assistant.session_controller import SessionController


# ── 模拟 Orchestrator 响应解析的简化版 ──

def _fake_parsed_response(mode="react", steps=None, thought=""):
    return {"mode": mode, "steps": steps or [], "thought": thought}


class TestFullConversationFlow:
    """模拟完整对话流程，验证 Controller 状态追踪。"""

    def _create_wired_controller(self):
        """创建模拟 ChatWidget 注入回调后的 Controller。"""
        orch = MagicMock()
        handler = MagicMock()
        handler._needs_confirm = lambda s: s.get("tool") in (
            "write_to_file", "start_translation")

        state_log = []
        sys_msgs = []
        plan_cards = []
        tool_cards = []

        controller = SessionController(
            orchestrator=orch,
            tool_handler=handler,
            on_state_changed=lambda old, new, ctx: state_log.append((old, new)),
            on_system_message=lambda m: sys_msgs.append(m),
            on_present_plan_card=lambda s: plan_cards.append(s),
            on_present_tool_card=lambda s: tool_cards.append(s),
            on_conversation_end=lambda: sys_msgs.append("[CONVERSATION_END]"),
        )
        return controller, state_log, sys_msgs, plan_cards, tool_cards, orch

    # ── 场景 1: 简单问候（无工具调用）─────────────────────────

    def test_simple_greeting_idle_to_idle(self):
        """用户打招呼 → LLM 纯文本回复 → 对话结束。"""
        controller, state_log, sys_msgs, _, _, _ = self._create_wired_controller()

        # 用户发送消息
        controller.handle_user_message("你好")
        assert controller.state == SessionController.State.THINKING
        assert len(state_log) == 1
        assert state_log[0] == (SessionController.State.IDLE, SessionController.State.THINKING)

        # LLM 返回纯文本（无工具调用）
        controller.handle_llm_response(_fake_parsed_response(mode="react", steps=[]))
        assert controller.state == SessionController.State.IDLE
        assert "[CONVERSATION_END]" in sys_msgs

    # ── 场景 2: 单工具 React 流程 ─────────────────────────────

    def test_single_tool_react_flow(self):
        """用户请求 → LLM 返回单工具 → 用户确认 → 执行 → 继续。"""
        controller, state_log, _, _, tool_cards, _ = self._create_wired_controller()

        # Step 1: 用户输入
        controller.handle_user_message("查看统计")
        assert controller.state == SessionController.State.THINKING

        # Step 2: LLM 返回单工具调用
        step = {"id": 1, "tool": "get_statistics", "args": {}}
        controller.handle_llm_response(_fake_parsed_response(
            mode="react", steps=[step], thought="检查当前翻译进度"))
        assert controller.state == SessionController.State.AWAITING_CONFIRM
        assert len(tool_cards) == 1

        # Step 3: 用户点击执行
        controller.handle_user_confirmed([step], "react")
        assert controller.state == SessionController.State.EXECUTING

        # Step 4: 工具执行完成，ReAct 继续
        controller.handle_execution_complete([])
        assert controller.state == SessionController.State.THINKING
        assert controller.react_depth == 1

    # ── 场景 3: Plan 模式完整流程 ────────────────────────────

    def test_plan_mode_full_flow(self):
        """用户请求 → LLM Plan → 用户确认 → DAG执行 → 完成。"""
        controller, state_log, _, plan_cards, _, _ = self._create_wired_controller()

        # Step 1: 用户输入
        controller.handle_user_message("解析并翻译这个文件")
        assert controller.state == SessionController.State.THINKING

        # Step 2: LLM 返回 plan
        steps = [
            {"id": 1, "tool": "parse_file", "args": {"path": "test.esp"}},
            {"id": 2, "tool": "start_translation", "args": {}, "depends_on": [1]},
        ]
        controller.handle_llm_response(_fake_parsed_response(
            mode="plan", steps=steps, thought="先解析再翻译"))
        assert controller.state == SessionController.State.AWAITING_CONFIRM
        assert len(plan_cards) == 1

        # Step 3: 用户确认计划
        controller.handle_user_confirmed(steps, "plan")
        assert controller.state == SessionController.State.EXECUTING

        # Step 4: 计划执行完成
        controller.handle_execution_complete([
            {"step_id": 1, "success": True},
            {"step_id": 2, "success": True},
        ])
        assert controller.state == SessionController.State.THINKING

    # ── 场景 4: 多步 React 批量确认 ──────────────────────────

    def test_multi_step_react_batch(self):
        """LLM 返回多步 React → 用户批量确认 → 执行完成。"""
        controller, state_log, _, _, _, _ = self._create_wired_controller()

        controller.handle_user_message("查看状态和统计")
        steps = [
            {"id": 1, "tool": "get_app_state", "args": {}},
            {"id": 2, "tool": "get_statistics", "args": {}},
        ]
        controller.handle_llm_response(_fake_parsed_response(
            mode="react", steps=steps, thought="先看全局状态再看统计"))

        assert controller.state == SessionController.State.AWAITING_CONFIRM

        # 用户批量确认
        controller.handle_user_confirmed(steps, "react")
        assert controller.state == SessionController.State.EXECUTING

        # 执行完成
        controller.handle_execution_complete([])
        assert controller.state == SessionController.State.THINKING

    # ── 场景 5: ReAct 多轮循环 ────────────────────────────────

    def test_react_multi_round_loop(self):
        """多轮 ReAct 循环，验证 react_depth 递增。"""
        controller, state_log, _, _, _, _ = self._create_wired_controller()

        # Round 1
        controller.handle_user_message("帮我翻译")
        assert controller.react_depth == 0
        step = {"id": 1, "tool": "get_statistics", "args": {}}
        controller.handle_llm_response(_fake_parsed_response(
            mode="react", steps=[step], thought="先看看有多少待翻译"))
        controller.handle_user_confirmed([step], "react")
        controller.handle_execution_complete([])
        # 自动进入 Round 2
        assert controller.state == SessionController.State.THINKING
        assert controller.react_depth == 1

        # Round 2: LLM 看了统计后决定翻译
        step2 = {"id": 2, "tool": "start_translation", "args": {}}
        controller.handle_llm_response(_fake_parsed_response(
            mode="react", steps=[step2], thought="开始翻译"))
        controller.handle_user_confirmed([step2], "react")
        controller.handle_execution_complete([])
        # 自动进入 Round 3
        assert controller.state == SessionController.State.THINKING
        assert controller.react_depth == 2

    # ── 场景 6: ReAct 深度上限 ────────────────────────────────

    def test_react_max_depth_terminates(self):
        """ReAct 深度达上限后自动终止。"""
        controller, state_log, sys_msgs, _, _, _ = self._create_wired_controller()

        controller.handle_user_message("start")
        step = {"id": 1, "tool": "get_statistics", "args": {}}

        # 模拟 MAX_REACT_DEPTH+2 轮完整的 ReAct（深度递增后检查，需 >MAX 触发上限）
        for i in range(SessionController._MAX_REACT_DEPTH + 2):
            if controller.state == SessionController.State.THINKING:
                controller.handle_llm_response(_fake_parsed_response(
                    mode="react", steps=[step], thought=f"round {i}"))
                controller.handle_user_confirmed([step], "react")
            elif controller.state == SessionController.State.AWAITING_CONFIRM:
                controller.handle_user_confirmed([step], "react")
            elif controller.state == SessionController.State.IDLE:
                break  # 已终止
            controller.handle_execution_complete([])

        # 达到上限后应终止到 IDLE
        assert controller.state == SessionController.State.IDLE, (
            f"期望 IDLE，实际 {controller.state}，depth={controller.react_depth}")
        assert controller.react_depth == 0
        assert any("深度" in m for m in sys_msgs)

    # ── 场景 7: 用户取消 ──────────────────────────────────────

    def test_user_cancels_tool(self):
        """用户看到工具卡片后取消。"""
        controller, state_log, sys_msgs, _, _, _ = self._create_wired_controller()

        controller.handle_user_message("写回文件")
        step = {"id": 1, "tool": "write_to_file", "args": {}}
        controller.handle_llm_response(_fake_parsed_response(
            mode="react", steps=[step], thought="需要写回"))

        assert controller.state == SessionController.State.AWAITING_CONFIRM

        # 用户取消
        controller.handle_user_cancelled()
        assert controller.state == SessionController.State.IDLE
        assert any("取消" in m for m in sys_msgs)

    # ── 场景 8: 强制中断 ──────────────────────────────────────

    def test_abort_from_thinking(self):
        """在 LLM 推理中途强制中断。"""
        controller, _, _, _, _, orch = self._create_wired_controller()

        controller.handle_user_message("开始一个长任务")
        assert controller.state == SessionController.State.THINKING

        controller.handle_abort()
        assert controller.state == SessionController.State.IDLE
        assert controller.react_depth == 0
        orch.cancel_current_round.assert_called_once()

    # ── 场景 9: 异步任务流程 ──────────────────────────────────

    def test_async_task_flow(self):
        """长运行工具启动 → 后台运行 → 完成通知。"""
        controller, state_log, _, _, _, _ = self._create_wired_controller()

        controller.handle_user_message("翻译全部")
        step = {"id": 1, "tool": "start_translation", "args": {}}
        controller.handle_llm_response(_fake_parsed_response(
            mode="react", steps=[step], thought="启动翻译"))
        controller.handle_user_confirmed([step], "react")
        assert controller.state == SessionController.State.EXECUTING

        # 工具是长运行任务 → AWAITING_TASK
        controller.handle_task_started()
        assert controller.state == SessionController.State.AWAITING_TASK

        # 后台任务完成
        controller.handle_task_completed("task_1", {"success_count": 10})
        assert controller.state == SessionController.State.THINKING

    # ── 场景 10: Orchestrator 回调集成验证 ────────────────────

    def test_orchestrator_on_response_parsed_integration(self):
        """验证 Orchestrator._on_finished 确实调用了 on_response_parsed。"""
        # 这个测试验证 Orchestrator 回调链完整
        from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator
        from transbridge.smart_assistant.conversation_manager import ConversationManager

        parsed_calls = []

        conv = ConversationManager()
        # 注意: Orchestrator 需要很多依赖，这里只验证回调属性存在
        # 完整的端到端测试需要启动 Qt 应用
        assert hasattr(ConversationOrchestrator, '__init__')

        # 验证 ConversationOrchestrator.__init__ 接受 on_response_parsed 参数
        import inspect
        sig = inspect.signature(ConversationOrchestrator.__init__)
        assert 'on_response_parsed' in sig.parameters, (
            "Orchestrator 缺少 on_response_parsed 参数！")


class TestCallbackWiringEquivalence:
    """验证新旧路径的回调等价性 — Controller 看到的状态转换应该与旧逻辑一致。"""

    def test_react_loop_state_sequence(self):
        """React 循环的状态序列：IDLE→THINKING→AWAITING→EXECUTING→THINKING→..."""
        controller = SessionController()
        seq = []

        controller.on_state_changed = lambda old, new, ctx: seq.append(new.value)

        step = {"id": 1, "tool": "get_statistics", "args": {}}

        # Round 1
        controller.handle_user_message("hi")
        controller.handle_llm_response(
            {"mode": "react", "steps": [step], "thought": ""})
        controller.handle_user_confirmed([step], "react")
        controller.handle_execution_complete([])

        # Round 2
        controller.handle_llm_response(
            {"mode": "react", "steps": [step], "thought": ""})
        controller.handle_user_confirmed([step], "react")
        controller.handle_execution_complete([])

        expected = [
            "thinking",           # IDLE → THINKING
            "awaiting",           # THINKING → AWAITING
            "executing",          # AWAITING → EXECUTING
            "thinking",           # EXECUTING → THINKING (round 1 done)
            "awaiting",           # THINKING → AWAITING
            "executing",          # AWAITING → EXECUTING
            "thinking",           # EXECUTING → THINKING (round 2 done)
        ]
        assert seq == expected, f"状态序列不匹配:\n期望: {expected}\n实际: {seq}"
