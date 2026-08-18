"""SessionController — 会话级状态机，统一管理 LLM 对话主循环控制流。

ADR-008 (D8-D12): 后端纯 Python，零 PyQt6 依赖。通过回调注入与 UI 通信。
位于 ConversationOrchestrator / ToolExecutionHandler / ExecutionEngine 之上，
管理会话级状态（IDLE→THINKING→AWAITING→EXECUTING），
不穿透 GraphExecutor 的执行级状态（ADR-011 双层状态机）。

Story 01: 新建 + 新旧并行。不删除 ChatWidget 中任何旧方法。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import logging
from typing import Any

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.sessions.models import ControllerSnapshot, ControllerState

logger = logging.getLogger(__name__)


class SessionTransitionError(DomainError):
    def __init__(self, action: str, current: str, expected: tuple[str, ...]) -> None:
        super().__init__(
            ErrorCategory.CONFLICT,
            "SESSION_STATE_TRANSITION_INVALID",
            "The Session controller action is invalid for its current state.",
            details={"action": action, "current": current, "expected": list(expected)},
        )


class SessionController:
    """会话级状态机。

    输入接口（供 UI/外部调用）:
        handle_user_message(text)
        handle_user_confirmed(steps, mode)
        handle_user_cancelled()
        handle_execution_complete(results)
        handle_task_completed(task_id, result)
        handle_abort()

    输出接口（回调注入，供 UI 响应）:
        on_state_changed(old, new, context)
        on_present_plan_card(steps)
        on_present_tool_card(step)
        on_present_batch_tool_card(steps)
        on_system_message(text)
        on_conversation_end()
    """

    class State(Enum):
        IDLE = "idle"
        THINKING = "thinking"
        AWAITING_CONFIRM = "awaiting"
        EXECUTING = "executing"
        AWAITING_TASK = "awaiting_task"

    _MAX_REACT_DEPTH = 10
    _TRANSITIONS = {
        State.IDLE: frozenset({State.IDLE, State.THINKING}),
        State.THINKING: frozenset({State.IDLE, State.AWAITING_CONFIRM, State.EXECUTING}),
        State.AWAITING_CONFIRM: frozenset({State.IDLE, State.EXECUTING}),
        State.EXECUTING: frozenset({State.IDLE, State.THINKING, State.AWAITING_TASK}),
        State.AWAITING_TASK: frozenset({State.IDLE, State.THINKING}),
    }

    def __init__(
        self,
        *,
        orchestrator: Any = None,
        tool_handler: Any = None,
        conversation: Any = None,
        # 输出回调
        on_state_changed: Callable | None = None,
        on_present_plan_card: Callable | None = None,
        on_present_tool_card: Callable | None = None,
        on_present_batch_tool_card: Callable | None = None,
        on_system_message: Callable | None = None,
        on_conversation_end: Callable | None = None,
        # UI 操作方法（Controller 需要触发的 UI 动作）
        on_llm_round_start: Callable | None = None,
        on_thinking_indicator_hide: Callable | None = None,
        # 权限检查
        on_check_permission: Callable | None = None,
    ):
        self._orchestrator = orchestrator
        self._tool_handler = tool_handler
        self._conversation = conversation

        # 输出回调
        self.on_state_changed = on_state_changed or (lambda *a: None)
        self.on_present_plan_card = on_present_plan_card or (lambda _: None)
        self.on_present_tool_card = on_present_tool_card or (lambda _: None)
        self.on_present_batch_tool_card = on_present_batch_tool_card or (lambda _: None)
        self.on_system_message = on_system_message or (lambda _: None)
        self.on_conversation_end = on_conversation_end or (lambda: None)

        # UI 操作回调
        self._on_llm_round_start = on_llm_round_start or (lambda: None)
        self._on_thinking_indicator_hide = on_thinking_indicator_hide or (lambda: None)
        self._on_check_permission = on_check_permission

        # 内部状态
        self._state: SessionController.State = self.State.IDLE
        self._react_depth: int = 0
        self._auto_mode: bool = False
        self._active_task: tuple[str, str] | None = None
        self._stale_task_events: list[tuple[str, str]] = []

    # ── 状态属性 ─────────────────────────────────────────────

    @property
    def state(self) -> State:
        return self._state

    @property
    def react_depth(self) -> int:
        return self._react_depth

    @property
    def auto_mode(self) -> bool:
        return self._auto_mode

    @auto_mode.setter
    def auto_mode(self, value: bool) -> None:
        self._auto_mode = value

    # ── 输入接口 ─────────────────────────────────────────────

    def handle_user_message(self, text: str) -> None:
        """IDLE → THINKING: 用户发送消息，启动 LLM 轮次。"""
        self._require("handle_user_message", self.State.IDLE)
        self._react_depth = 0
        self._transition_to(self.State.THINKING)
        self._on_llm_round_start()

    def handle_llm_response(self, parsed: dict) -> None:
        """THINKING → AWAITING_CONFIRM | EXECUTING | IDLE: LLM 响应到达，按模式分发。"""
        self._require("handle_llm_response", self.State.THINKING)

        self._on_thinking_indicator_hide()

        steps = parsed.get("steps", [])
        mode = parsed.get("mode", "react")

        if not steps:
            # 纯文本回复，对话结束
            self._transition_to(self.State.IDLE)
            self.on_conversation_end()
            return

        if self._auto_mode and mode != "plan" and not self._any_needs_confirm(steps):
            # 自动模式 + ReAct + 无需确认 → 直接执行
            # 注意: plan 模式始终走确认流程，因为 _execute_plan 是 no-op，
            # 实际执行依赖 ChatWidget._on_plan_confirmed 创建 ExecutionEngine
            self._transition_to(self.State.EXECUTING)
            self._dispatch_steps(steps, mode)
            # 执行完成后自动触发下一轮（ReAct 继续）
            if self._state is self.State.EXECUTING:
                self.handle_execution_complete([])
        else:
            # 需要用户确认 → 展示确认卡片
            self._transition_to(self.State.AWAITING_CONFIRM)
            if mode == "plan" and len(steps) > 1:
                self.on_present_plan_card(steps)
            elif len(steps) == 1:
                self.on_present_tool_card(steps[0])
            else:
                self.on_present_batch_tool_card(steps)

    def handle_user_confirmed(self, steps: list, mode: str = "react") -> None:
        """AWAITING_CONFIRM → EXECUTING: 用户确认执行。"""
        self._require("handle_user_confirmed", self.State.AWAITING_CONFIRM)
        self._transition_to(self.State.EXECUTING)
        self._dispatch_steps(steps, mode)

    def handle_user_cancelled(self) -> None:
        """AWAITING_CONFIRM → IDLE: 用户取消操作。"""
        self._require("handle_user_cancelled", self.State.AWAITING_CONFIRM)
        self._transition_to(self.State.IDLE)
        self.on_system_message("操作已取消")

    def handle_execution_complete(self, results: list) -> None:
        """EXECUTING → THINKING | IDLE: 计划/工具执行完成，决定是否继续 ReAct 循环。"""
        self._require("handle_execution_complete", self.State.EXECUTING)

        if self._react_depth > self._MAX_REACT_DEPTH:
            logger.info("ReAct 深度已达上限 %d，终止循环", self._MAX_REACT_DEPTH)
            self._react_depth = 0
            self._transition_to(self.State.IDLE)
            self.on_system_message("已达最大推理深度，对话终止。")
            self.on_conversation_end()
        else:
            self._react_depth += 1
            logger.debug("ReAct 深度 %d/%d，继续下一轮 LLM",
                         self._react_depth, self._MAX_REACT_DEPTH)
            self._transition_to(self.State.THINKING)
            self._on_llm_round_start()

    def handle_task_completed(self, task_id: str, result: dict, run_id: str = "") -> None:
        """AWAITING_TASK → THINKING | IDLE: 异步后台任务完成。"""
        if self._active_task is not None:
            expected_task_id, expected_run_id = self._active_task
            if task_id != expected_task_id or (run_id and run_id != expected_run_id):
                self._stale_task_events.append((task_id, run_id))
                logger.warning("Ignoring stale task completion task=%s run=%s", task_id, run_id)
                return
        self._require("handle_task_completed", self.State.AWAITING_TASK)
        self._active_task = None

        if self._react_depth > self._MAX_REACT_DEPTH:
            self._react_depth = 0
            self._transition_to(self.State.IDLE)
            self.on_system_message("已达最大推理深度，对话终止。")
            self.on_conversation_end()
        else:
            self._react_depth += 1
            self._transition_to(self.State.THINKING)
            self._on_llm_round_start()

    def handle_task_started(self, task_id: str = "", run_id: str = "") -> None:
        """EXECUTING → AWAITING_TASK: 长运行异步任务已启动，等待完成通知。"""
        self._require("handle_task_started", self.State.EXECUTING)
        self._active_task = (task_id, run_id) if task_id else None
        self._transition_to(self.State.AWAITING_TASK)

    def handle_abort(self) -> None:
        """任意状态 → IDLE: 强制中断当前操作。"""
        logger.info("SessionController: 强制中断，状态 %s → IDLE", self._state)
        if self._orchestrator is not None:
            try:
                self._orchestrator.cancel_current_round()
            except Exception:
                pass
        self._react_depth = 0
        self._active_task = None
        self._transition_to(self.State.IDLE)

    # ── 内部方法 ─────────────────────────────────────────────

    def _transition_to(self, new_state: State) -> None:
        """执行状态转换并触发回调。"""
        old_state = self._state
        if new_state not in self._TRANSITIONS[old_state]:
            raise SessionTransitionError(
                "transition",
                old_state.value,
                tuple(item.value for item in self._TRANSITIONS[old_state]),
            )
        self._state = new_state
        logger.debug("SessionController: %s → %s", old_state.value, new_state.value)
        self.on_state_changed(old_state, new_state, {})

    def to_recovery_snapshot(self) -> ControllerSnapshot:
        state = ControllerState(self._state.value)
        recoverable = state in {ControllerState.IDLE, ControllerState.AWAITING_CONFIRM}
        return ControllerSnapshot(
            state,
            self._react_depth,
            self._auto_mode,
            recoverable,
            None if recoverable else "in_flight_controller_state_requires_job_reconciliation",
        )

    def restore_recovery_snapshot(self, snapshot: ControllerSnapshot) -> ControllerSnapshot:
        if snapshot.recoverable and snapshot.state in {
            ControllerState.IDLE,
            ControllerState.AWAITING_CONFIRM,
        }:
            self._state = self.State(snapshot.state.value)
            self._react_depth = snapshot.react_depth
            self._auto_mode = snapshot.auto_mode
            return snapshot
        degraded = ControllerSnapshot(
            ControllerState.IDLE,
            0,
            snapshot.auto_mode,
            False,
            snapshot.reason or "controller_state_not_recoverable",
        )
        self._state = self.State.IDLE
        self._react_depth = 0
        self._auto_mode = snapshot.auto_mode
        return degraded

    def _require(self, action: str, *expected: State) -> None:
        if self._state not in expected:
            raise SessionTransitionError(
                action,
                self._state.value,
                tuple(item.value for item in expected),
            )

    def _any_needs_confirm(self, steps: list) -> bool:
        """检查步骤列表中是否有需要用户确认的工具。"""
        if self._tool_handler is None:
            return False
        return any(
            self._tool_handler._needs_confirm(s) for s in steps
        )

    def _dispatch_steps(self, steps: list, mode: str) -> None:
        """根据模式分发步骤执行。

        plan 模式 → 通过 ExecutionEngine 执行。
        react 模式 → 通过 ToolExecutionHandler 逐个执行。
        """
        if mode == "plan":
            self._execute_plan(steps)
        else:
            self._execute_react(steps)

    def _execute_plan(self, steps: list) -> None:
        """计划模式：委托给 ExecutionEngine DAG 执行。

        调用方（ChatWidget._on_plan_confirmed）负责创建引擎并提交。
        Controller 只标记状态已转换到 EXECUTING。
        """
        logger.debug("SessionController: 进入 plan 执行模式 (%d 步骤)", len(steps))
        # plan 的实际执行由 ChatWidget._on_plan_confirmed 处理
        # 它创建 ExecutionEngine 并注册回调，在 _on_plan_all_finished 中调用 handle_execution_complete

    def _execute_react(self, steps: list) -> None:
        """ReAct 模式：逐个执行工具步骤（跳过每步完成回调，由调用方统一触发）。"""
        logger.debug("SessionController: 进入 ReAct 执行模式 (%d 步骤)", len(steps))
        if self._tool_handler is None:
            logger.warning("SessionController: tool_handler 未注入，跳过 React 执行")
            return
        for s in steps:
            self._tool_handler.execute_step(s, skip_react_continue=True)
