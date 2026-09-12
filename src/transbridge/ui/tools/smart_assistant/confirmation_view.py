from __future__ import annotations

from collections.abc import Callable
import threading
from weakref import WeakSet

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from .plan_card import PlanCard
from .plan_execution_binding import PlanExecutionBinding
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
        self._generation = 0
        self._pending_cards: WeakSet = WeakSet()
        self._bridge = _MainThreadBridge(parent)
        self._pending_events: set[threading.Event] = set()
        self._pending_lock = threading.Lock()

    def add_tool_card(self, step: dict) -> ToolCard | None:
        if self._closed:
            return None
        card = ToolCard(step, theme=self._theme)
        self._connect_intent(card, card.executed, self._tool_executed)
        self._connect_intent(card, card.ignored, self._tool_ignored, accepted=False)
        self._add_widget(card)
        return card

    def add_plan_card(self, steps: list) -> PlanCard | None:
        if self._closed:
            return None
        card = PlanCard(steps, theme=self._theme)
        self._connect_intent(card, card.confirmed, self._plan_confirmed)
        self._connect_intent(card, card.cancelled, self._plan_cancelled, accepted=False)
        self._add_widget(card)
        return card

    def add_batch_tool_card(self, steps: list) -> BatchToolCard | None:
        if self._closed:
            return None
        card = BatchToolCard(steps, theme=self._theme)
        self._connect_intent(card, card.all_executed, self._batch_executed)
        self._connect_intent(card, card.all_ignored, self._batch_ignored, accepted=False)
        self._add_widget(card)
        return card

    def _connect_intent(self, card, signal, callback: Callable, *, accepted=True) -> None:
        generation = self._generation
        scope = getattr(self, "intent_scope", None)
        validate = scope(accepted=accepted) if scope is not None else lambda: True
        self._pending_cards.add(card)

        def deliver(*args) -> None:
            if self._closed or generation != self._generation or card not in self._pending_cards:
                return
            self._pending_cards.discard(card)
            if validate():
                callback(*args)

        signal.connect(deliver)

    def invalidate_pending(self) -> None:
        """Expire confirmation capabilities before replacing their owning request."""
        with self._pending_lock:
            self._generation += 1
            pending = tuple(self._pending_events)
            self._pending_events.clear()
        for card in tuple(self._pending_cards):
            if not sip.isdeleted(card):
                card.expire()
        self._pending_cards.clear()
        for event in pending:
            event.set()

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme

    def dispatch(self, callback: Callable[[], None]) -> None:
        """Queue a non-blocking presentation callback on the GUI thread."""
        if not self._closed:
            self._bridge.requested.emit(callback)

    def request_engine_decision(
        self,
        node_id: str,
        prompt: str,
        choices: list,
        *,
        engine=None,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        if self._closed or not choices:
            return
        source_engine = engine if engine is not None else self._engine()
        generation = self._generation

        def active() -> bool:
            return (
                not self._closed
                and generation == self._generation
                and source_engine is not None
                and self._engine() is source_engine
                and (is_current is None or is_current())
            )

        if not active():
            return
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() == app.thread():
            self._show_dialog(node_id, prompt, choices, source_engine, active)
            return
        done = threading.Event()
        with self._pending_lock:
            if not active():
                return
            self._pending_events.add(done)

        def on_main_thread() -> None:
            try:
                if active():
                    self._show_dialog(node_id, prompt, choices, source_engine, active)
            finally:
                with self._pending_lock:
                    self._pending_events.discard(done)
                done.set()

        self._bridge.requested.emit(on_main_thread)
        done.wait()

    def ask_permission(self, title: str, message: str) -> bool:
        if self._closed:
            return False
        generation = self._generation
        reply = QMessageBox.question(
            self._parent,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return not self._closed and generation == self._generation and reply == QMessageBox.StandardButton.Yes

    def close(self) -> None:
        with self._pending_lock:
            if self._closed:
                return
            self._closed = True
        self.invalidate_pending()

    def _show_dialog(self, node_id: str, prompt: str, choices: list, engine, active: Callable[[], bool]) -> None:
        if not active():
            return
        reply = QMessageBox.question(
            self._parent,
            "操作确认",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        choice = choices[0] if reply == QMessageBox.StandardButton.Yes else (choices[1] if len(choices) > 1 else "跳过")
        if active():
            engine.provide_decision(node_id, choice)


__all__ = ["ConfirmationView", "PlanExecutionBinding"]


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
        pending = controller.handle_user_confirmed([step], "react")
        if not pending and getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])

    def ignore_tool(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        self._system_message(f"已忽略: {tool_name}")
        self._controller().handle_user_cancelled()

    def execute_batch(self, steps: list) -> None:
        controller = self._controller()
        pending = controller.handle_user_confirmed(steps, "react")
        self._hide_thinking()
        if not pending and getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])

    def ignore_batch(self, steps: list) -> None:
        tool_names = [step.get("tool", "?") for step in steps]
        self._system_message("已跳过: " + ", ".join(tool_names))
        self._controller().handle_user_cancelled()


__all__.append("ConfirmationActions")
