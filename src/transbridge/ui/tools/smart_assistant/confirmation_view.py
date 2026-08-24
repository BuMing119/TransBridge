from __future__ import annotations

from collections.abc import Callable
import threading

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from .plan_card import PlanCard
from .theme_support import SmartAssistantTheme
from .tool_card import BatchToolCard, ToolCard


class _MainThreadBridge(QObject):
    requested = pyqtSignal(object)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.requested.connect(
            lambda callback: callback(),
            Qt.ConnectionType.QueuedConnection,
        )


class ConfirmationView:
    """Creates confirmation widgets and emits each user intent once."""

    def __init__(
        self,
        *,
        parent,
        add_widget: Callable[[object], None],
        plan_confirmed: Callable[[list], None],
        plan_cancelled: Callable[[], None],
        tool_executed: Callable[[dict], None],
        tool_ignored: Callable[[dict], None],
        batch_executed: Callable[[list], None],
        batch_ignored: Callable[[list], None],
        engine: Callable[[], object | None],
        theme: SmartAssistantTheme | None = None,
    ) -> None:
        self._parent = parent
        self._add_widget = add_widget
        self._plan_confirmed = plan_confirmed
        self._plan_cancelled = plan_cancelled
        self._tool_executed = tool_executed
        self._tool_ignored = tool_ignored
        self._batch_executed = batch_executed
        self._batch_ignored = batch_ignored
        self._engine = engine
        self._theme = theme or SmartAssistantTheme()
        self._closed = False
        self._bridge = _MainThreadBridge(parent)
        self._pending_events: set[threading.Event] = set()
        self._pending_lock = threading.Lock()

    def add_tool_card(self, step: dict) -> ToolCard | None:
        if self._closed:
            return None
        card = ToolCard(step, theme=self._theme)
        card.executed.connect(self._tool_executed)
        card.ignored.connect(self._tool_ignored)
        self._add_widget(card)
        return card

    def add_plan_card(self, steps: list) -> PlanCard | None:
        if self._closed:
            return None
        card = PlanCard(steps, theme=self._theme)
        card.confirmed.connect(self._plan_confirmed)
        card.cancelled.connect(self._plan_cancelled)
        self._add_widget(card)
        return card

    def add_batch_tool_card(self, steps: list) -> BatchToolCard | None:
        if self._closed:
            return None
        card = BatchToolCard(steps, theme=self._theme)
        card.all_executed.connect(self._batch_executed)
        card.all_ignored.connect(self._batch_ignored)
        self._add_widget(card)
        return card

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme

    def dispatch(self, callback: Callable[[], None]) -> None:
        """Queue a non-blocking presentation callback on the GUI thread."""
        if not self._closed:
            self._bridge.requested.emit(callback)

    def request_engine_decision(self, node_id: str, prompt: str, choices: list) -> None:
        if self._closed or not choices:
            return
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() == app.thread():
            self._show_dialog(node_id, prompt, choices)
            return
        done = threading.Event()
        with self._pending_lock:
            if self._closed:
                return
            self._pending_events.add(done)
        displayed: list[bool] = []

        def on_main_thread() -> None:
            try:
                if not self._closed:
                    self._show_dialog(node_id, prompt, choices)
                    displayed.append(True)
            finally:
                with self._pending_lock:
                    self._pending_events.discard(done)
                done.set()

        self._bridge.requested.emit(on_main_thread)
        done.wait()
        if not displayed and not self._closed:
            engine = self._engine()
            if engine is not None:
                engine.provide_decision(node_id, choices[0])

    def ask_permission(self, title: str, message: str) -> bool:
        if self._closed:
            return False
        return (
            QMessageBox.question(
                self._parent,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def close(self) -> None:
        with self._pending_lock:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._pending_events)
            self._pending_events.clear()
        for event in pending:
            event.set()

    def _show_dialog(self, node_id: str, prompt: str, choices: list) -> None:
        if self._closed:
            return
        reply = QMessageBox.question(
            self._parent,
            "操作确认",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        choice = choices[0] if reply == QMessageBox.StandardButton.Yes else (choices[1] if len(choices) > 1 else "跳过")
        engine = self._engine()
        if engine is not None and not self._closed:
            engine.provide_decision(node_id, choice)


__all__ = ["ConfirmationView"]


class PlanExecutionBinding:
    """Owns the legacy plan engine while SessionController remains authoritative."""

    def __init__(
        self,
        *,
        context,
        controller: Callable[[], object],
        middlewares: Callable[[], list],
        observability,
        conversation,
        hide_thinking: Callable[[], None],
        system_message: Callable[[str], None],
    ) -> None:
        self._context = context
        self._controller = controller
        self._middlewares = middlewares
        self._observability = observability
        self._conversation = conversation
        self._hide_thinking = hide_thinking
        self._system_message = system_message
        self._engine = None
        self._closed = False

    @property
    def engine(self):
        return self._engine

    def confirm(self, steps: list, confirmation_view: ConfirmationView) -> None:
        if self._closed:
            return
        from transbridge.smart_assistant.execution_engine import ExecutionEngine
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        self._controller().handle_user_confirmed(steps, "plan")
        self._hide_thinking()
        self._dispose_engine()
        engine = ExecutionEngine(ToolRegistry, self._context, middlewares=self._middlewares())
        self._engine = engine
        engine.on_all_finished(lambda results: confirmation_view.dispatch(lambda: self._on_all_finished(results)))
        engine.on_step_requires_confirmation(confirmation_view.request_engine_decision)
        self._observability.start_conversation(f"conv_{id(steps)}")
        engine.on_step_started(self._observability.on_step_started)
        engine.on_step_finished(self._observability.on_step_finished)
        engine.on_step_retrying(self._observability.on_step_retrying)
        engine._executor._executor.submit(engine.execute, steps)

    def cancel(self) -> None:
        if self._closed:
            return
        if self._engine is not None:
            self._engine.cancel()
        self._hide_thinking()
        self._system_message("计划已取消")

    def abort(self) -> None:
        self._dispose_engine()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dispose_engine()

    def _on_all_finished(self, results: list) -> None:
        if self._closed:
            return
        lines = []
        for result in results:
            icon = "[OK]" if result.success else "[FAIL]"
            lines.append(f"{icon} 步骤 {result.step_id} ({result.tool}): {result.message}")
        summary = "\n".join(lines)
        self._system_message(f"【计划执行完成】\n{summary}")
        self._conversation.add_plan_result(summary)
        self._observability.end_conversation()
        self._controller().handle_execution_complete(results)

    def _dispose_engine(self) -> None:
        engine = self._engine
        self._engine = None
        if engine is not None:
            engine.cancel()
            engine.shutdown()


__all__.append("PlanExecutionBinding")


class ConfirmationActions:
    """Maps confirmation-card intents onto the authoritative controller."""

    def __init__(
        self,
        *,
        controller: Callable[[], object],
        conversation,
        hide_thinking: Callable[[], None],
        system_message: Callable[[str], None],
    ) -> None:
        self._controller = controller
        self._conversation = conversation
        self._hide_thinking = hide_thinking
        self._system_message = system_message

    def execute_tool(self, step: dict) -> None:
        self._hide_thinking()
        controller = self._controller()
        controller.handle_user_confirmed([step], "react")
        controller.handle_execution_complete([])

    def ignore_tool(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        self._system_message(f"已忽略: {tool_name}")
        self._conversation.add_observation(tool_name, "用户选择不执行此操作。")
        self._controller().handle_user_cancelled()

    def execute_batch(self, steps: list) -> None:
        controller = self._controller()
        controller.handle_user_confirmed(steps, "react")
        self._hide_thinking()
        controller.handle_execution_complete([])

    def ignore_batch(self, steps: list) -> None:
        tool_names = [step.get("tool", "?") for step in steps]
        self._system_message("已跳过: " + ", ".join(tool_names))
        for name in tool_names:
            self._conversation.add_observation(name, "用户选择跳过此批量操作。")
        self._controller().handle_user_cancelled()


__all__.append("ConfirmationActions")
