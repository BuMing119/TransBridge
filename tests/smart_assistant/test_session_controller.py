"""SessionController 单元测试 — 覆盖所有状态转换与边界条件。

FR12 Story 01: 核心状态机验证。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from transbridge.smart_assistant.session_controller import SessionController, SessionTransitionError


class TestSessionControllerInit:
    """初始状态与属性测试。"""

    def test_initial_state_is_idle(self):
        controller = SessionController()
        assert controller.state == SessionController.State.IDLE

    def test_initial_react_depth_is_zero(self):
        controller = SessionController()
        assert controller.react_depth == 0

    def test_auto_mode_defaults_to_false(self):
        controller = SessionController()
        assert controller.auto_mode is False

    def test_auto_mode_setter(self):
        controller = SessionController()
        controller.auto_mode = True
        assert controller.auto_mode is True
        controller.auto_mode = False
        assert controller.auto_mode is False


class TestStateTransitions:
    """核心状态转换测试。"""

    # ── handle_user_message ──────────────────────────────────

    def test_user_message_idle_to_thinking(self):
        controller = SessionController()
        controller.handle_user_message("hello")
        assert controller.state == SessionController.State.THINKING

    def test_user_message_resets_react_depth(self):
        controller = SessionController()
        # Manually set depth via internal access for test
        controller._react_depth = 5
        controller.handle_user_message("hello")
        assert controller.react_depth == 0

    def test_user_message_asserts_if_not_idle(self):
        controller = SessionController()
        controller.handle_user_message("first")
        # Now in THINKING
        with pytest.raises(SessionTransitionError):
            controller.handle_user_message("second")

    # ── handle_llm_response ──────────────────────────────────

    def test_llm_response_no_steps_thinking_to_idle(self):
        controller = SessionController()
        controller.handle_user_message("hello")
        controller.handle_llm_response({"mode": "react", "steps": [], "thought": ""})
        assert controller.state == SessionController.State.IDLE

    def test_llm_response_no_steps_fires_conversation_end(self):
        end_called = []
        controller = SessionController(on_conversation_end=lambda: end_called.append(True))
        controller.handle_user_message("hello")
        controller.handle_llm_response({"mode": "react", "steps": [], "thought": ""})
        assert len(end_called) == 1

    def test_llm_response_with_steps_thinking_to_awaiting(self):
        controller = SessionController()
        controller.handle_user_message("translate this")
        steps = [{"id": 1, "tool": "get_statistics", "args": {}}]
        controller.handle_llm_response({"mode": "react", "steps": steps, "thought": "checking"})
        assert controller.state == SessionController.State.AWAITING_CONFIRM

    def test_llm_response_single_step_presents_tool_card(self):
        cards = []
        controller = SessionController(on_present_tool_card=lambda s: cards.append(s))
        controller.handle_user_message("hi")
        step = {"id": 1, "tool": "get_statistics", "args": {}}
        controller.handle_llm_response({"mode": "react", "steps": [step], "thought": ""})
        assert len(cards) == 1
        assert cards[0] == step

    def test_llm_response_plan_mode_presents_plan_card(self):
        cards = []
        controller = SessionController(on_present_plan_card=lambda s: cards.append(s))
        controller.handle_user_message("do everything")
        steps = [
            {"id": 1, "tool": "parse_file", "args": {}},
            {"id": 2, "tool": "start_translation", "args": {}, "depends_on": [1]},
        ]
        controller.handle_llm_response({"mode": "plan", "steps": steps, "thought": "planning"})
        assert len(cards) == 1
        assert cards[0] == steps

    def test_llm_response_multi_step_react_presents_batch_card(self):
        cards = []
        controller = SessionController(on_present_batch_tool_card=lambda s: cards.append(s))
        controller.handle_user_message("do multiple things")
        steps = [
            {"id": 1, "tool": "get_statistics", "args": {}},
            {"id": 2, "tool": "get_app_state", "args": {}},
        ]
        controller.handle_llm_response({"mode": "react", "steps": steps, "thought": ""})
        assert len(cards) == 1
        assert cards[0] == steps

    def test_llm_response_auto_mode_no_confirm_direct_execute(self):
        controller = SessionController()
        controller.auto_mode = True
        controller.handle_user_message("do it")
        steps = [{"id": 1, "tool": "get_statistics", "args": {}}]
        controller.handle_llm_response({"mode": "react", "steps": steps, "thought": ""})
        # auto_mode + no confirm → tool_handler=None跳过执行 → 触发完成 → THINKING
        assert controller.state == SessionController.State.THINKING

    def test_llm_response_asserts_if_not_thinking(self):
        controller = SessionController()
        with pytest.raises(SessionTransitionError):
            controller.handle_llm_response({"mode": "react", "steps": [], "thought": ""})

    # ── handle_user_confirmed ────────────────────────────────

    def test_user_confirmed_awaiting_to_executing(self):
        controller = SessionController()
        controller.handle_user_message("hi")
        step = {"id": 1, "tool": "get_statistics", "args": {}}
        controller.handle_llm_response({"mode": "react", "steps": [step], "thought": ""})
        # Now AWAITING_CONFIRM
        controller.handle_user_confirmed([step], "react")
        assert controller.state == SessionController.State.EXECUTING

    def test_user_confirmed_asserts_if_not_awaiting(self):
        controller = SessionController()
        with pytest.raises(SessionTransitionError):
            controller.handle_user_confirmed([], "react")

    # ── handle_user_cancelled ────────────────────────────────

    def test_user_cancelled_awaiting_to_idle(self):
        controller = SessionController()
        controller.handle_user_message("hi")
        controller.handle_llm_response(
            {"mode": "react", "steps": [{"id": 1, "tool": "x", "args": {}}], "thought": ""})
        controller.handle_user_cancelled()
        assert controller.state == SessionController.State.IDLE

    def test_user_cancelled_shows_system_message(self):
        msgs = []
        controller = SessionController(on_system_message=lambda m: msgs.append(m))
        controller.handle_user_message("hi")
        controller.handle_llm_response(
            {"mode": "react", "steps": [{"id": 1, "tool": "x", "args": {}}], "thought": ""})
        controller.handle_user_cancelled()
        assert len(msgs) == 1

    def test_user_cancelled_asserts_if_not_awaiting(self):
        controller = SessionController()
        with pytest.raises(SessionTransitionError):
            controller.handle_user_cancelled()

    # ── handle_execution_complete ────────────────────────────

    def test_execution_complete_executing_to_thinking(self):
        controller = SessionController()
        controller.handle_user_message("hi")
        step = {"id": 1, "tool": "get_statistics", "args": {}}
        controller.handle_llm_response({"mode": "react", "steps": [step], "thought": ""})
        controller.handle_user_confirmed([step], "react")
        # Now EXECUTING, react_depth = 0
        controller.handle_execution_complete([])
        # react_depth incremented, still < MAX → THINKING
        assert controller.state == SessionController.State.THINKING
        assert controller.react_depth == 1

    def test_execution_complete_max_depth_to_idle(self):
        controller = SessionController()
        # > MAX 才触发终止（与旧 ChatWidget._check_react_depth 行为一致）
        controller._react_depth = SessionController._MAX_REACT_DEPTH + 1
        # Manually set state to EXECUTING (bypass normal flow for test)
        controller._state = SessionController.State.EXECUTING
        controller.handle_execution_complete([])
        assert controller.state == SessionController.State.IDLE
        assert controller.react_depth == 0

    def test_execution_complete_asserts_if_not_executing(self):
        controller = SessionController()
        with pytest.raises(SessionTransitionError):
            controller.handle_execution_complete([])

    # ── handle_task_started ──────────────────────────────────

    def test_task_started_executing_to_awaiting_task(self):
        controller = SessionController()
        # Bypass to EXECUTING
        controller.handle_user_message("hi")
        step = {"id": 1, "tool": "start_translation", "args": {}}
        controller.handle_llm_response({"mode": "react", "steps": [step], "thought": ""})
        controller.handle_user_confirmed([step], "react")
        # Now EXECUTING
        controller.handle_task_started()
        assert controller.state == SessionController.State.AWAITING_TASK

    def test_task_started_asserts_if_not_executing(self):
        controller = SessionController()
        with pytest.raises(SessionTransitionError):
            controller.handle_task_started()

    # ── handle_task_completed ────────────────────────────────

    def test_task_completed_awaiting_task_to_thinking(self):
        controller = SessionController()
        # Bypass to AWAITING_TASK
        controller.handle_user_message("hi")
        step = {"id": 1, "tool": "start_translation", "args": {}}
        controller.handle_llm_response({"mode": "react", "steps": [step], "thought": ""})
        controller.handle_user_confirmed([step], "react")
        controller.handle_task_started()
        # Now AWAITING_TASK
        controller.handle_task_completed("task_1", {"success": True})
        assert controller.state == SessionController.State.THINKING
        assert controller.react_depth == 1

    def test_stale_task_completion_cannot_advance_active_awaiting_session(self):
        controller = SessionController()
        controller._state = SessionController.State.EXECUTING
        controller.handle_task_started("task-active", "run-active")

        controller.handle_task_completed("task-old", {"success": True}, "run-old")

        assert controller.state == SessionController.State.AWAITING_TASK
        controller.handle_task_completed("task-active", {"success": True}, "run-active")
        assert controller.state == SessionController.State.THINKING

    def test_task_completed_max_depth_to_idle(self):
        controller = SessionController()
        # > MAX 才触发终止
        controller._react_depth = SessionController._MAX_REACT_DEPTH + 1
        controller._state = SessionController.State.AWAITING_TASK
        controller.handle_task_completed("task_1", {"success": True})
        assert controller.state == SessionController.State.IDLE
        assert controller.react_depth == 0

    def test_task_completed_asserts_if_not_awaiting_task(self):
        controller = SessionController()
        with pytest.raises(SessionTransitionError):
            controller.handle_task_completed("t1", {})

    # ── handle_abort ─────────────────────────────────────────

    def test_abort_any_state_to_idle(self):
        controller = SessionController()
        controller.handle_user_message("hi")
        assert controller.state == SessionController.State.THINKING
        controller.handle_abort()
        assert controller.state == SessionController.State.IDLE

    def test_abort_resets_react_depth(self):
        controller = SessionController()
        controller._react_depth = 5
        controller._state = SessionController.State.EXECUTING
        controller.handle_abort()
        assert controller.react_depth == 0

    def test_abort_cancels_orchestrator_if_present(self):
        orch = MagicMock()
        controller = SessionController(orchestrator=orch)
        controller._state = SessionController.State.THINKING
        controller.handle_abort()
        orch.cancel_current_round.assert_called_once()


class TestStateChangeCallback:
    """状态变更回调测试。"""

    def test_callback_fires_on_transition(self):
        transitions = []
        controller = SessionController(
            on_state_changed=lambda old, new, ctx: transitions.append((old, new)))
        controller.handle_user_message("hi")
        assert len(transitions) == 1
        assert transitions[0] == (SessionController.State.IDLE, SessionController.State.THINKING)

    def test_callback_fires_on_multiple_transitions(self):
        transitions = []
        controller = SessionController(
            on_state_changed=lambda old, new, ctx: transitions.append((old, new)))
        controller.handle_user_message("hi")
        step = {"id": 1, "tool": "get_statistics", "args": {}}
        controller.handle_llm_response({"mode": "react", "steps": [step], "thought": ""})
        controller.handle_user_confirmed([step], "react")
        controller.handle_execution_complete([])
        assert len(transitions) == 4
        expected = [
            (SessionController.State.IDLE, SessionController.State.THINKING),
            (SessionController.State.THINKING, SessionController.State.AWAITING_CONFIRM),
            (SessionController.State.AWAITING_CONFIRM, SessionController.State.EXECUTING),
            (SessionController.State.EXECUTING, SessionController.State.THINKING),
        ]
        assert transitions == expected


class TestNeedsConfirmDelegation:
    """_any_needs_confirm 委托测试。"""

    def test_any_needs_confirm_without_tool_handler(self):
        controller = SessionController()
        assert controller._any_needs_confirm([{"tool": "write", "args": {}}]) is False

    def test_any_needs_confirm_delegates_to_tool_handler(self):
        handler = MagicMock()
        handler._needs_confirm = lambda s: s.get("tool") == "write_to_file"
        controller = SessionController(tool_handler=handler)
        assert controller._any_needs_confirm([{"tool": "get_statistics", "args": {}}]) is False
        assert controller._any_needs_confirm([{"tool": "write_to_file", "args": {}}]) is True
