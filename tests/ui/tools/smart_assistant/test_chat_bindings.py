from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.shell.tool_windows import ToolWindows
from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.message_list_view import MessageListView
from transbridge.ui.tools.smart_assistant.panel import SmartAssistantPanel
from transbridge.ui.tools.smart_assistant.session_binding import ConversationBinding, SessionBinding
from transbridge.ui.tools.smart_assistant.streaming_presenter import StreamingPresenter
from transbridge.ui.tools.smart_assistant.task_binding import TaskBinding

_APP = QApplication.instance() or QApplication([])


def _message_list() -> tuple[MessageListView, QVBoxLayout]:
    scroll = QScrollArea()
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.addStretch()
    scroll.setWidget(container)
    button = QPushButton("bottom", scroll)
    return (
        MessageListView(
            layout,
            scroll_area=scroll,
            back_to_bottom_button=button,
            timer_parent=container,
            max_visible_widgets=2,
        ),
        layout,
    )


def test_message_list_enforces_limit_and_close_is_idempotent() -> None:
    view, layout = _message_list()
    view.add_bubble(MessageBubble("one", "user"))
    view.add_bubble(MessageBubble("two", "user"))
    view.add_bubble(MessageBubble("three", "user"))

    assert layout.count() - 1 == 2
    view.close()
    view.close()
    assert view.closed is True


def test_streaming_presenter_ignores_removed_and_closed_bubbles() -> None:
    view, _ = _message_list()
    bubble = MessageBubble("initial", "assistant")
    view.add_bubble(bubble)
    presenter = StreamingPresenter(view)

    presenter.flush("current", bubble)
    assert bubble._content.text() == "current"

    view.remove(bubble)
    presenter.flush("late", bubble)
    assert bubble._content.text() == "current"
    presenter.close()
    presenter.close()


class _Conversation:
    def __init__(self) -> None:
        self.messages = [{"role": "user", "content": "hello"}]

    def to_dict(self) -> dict:
        return {"messages": list(self.messages)}

    def from_dict(self, value: dict) -> None:
        self.messages = list(value["messages"])


class _SessionManager:
    def __init__(self) -> None:
        self.saved: list[tuple[str, list[dict]]] = []
        self.name = "新对话"
        self.renamed: list[tuple[str, str]] = []

    def save_session(self, session_id: str, messages: list[dict]) -> None:
        self.saved.append((session_id, list(messages)))

    def get_session(self, session_id: str) -> dict:
        return {"name": self.name, "messages": []}

    def rename_session(self, session_id: str, name: str) -> None:
        self.renamed.append((session_id, name))


def test_session_binding_uses_explicit_active_session_port_and_stops_after_close() -> None:
    conversation = _Conversation()
    manager = _SessionManager()
    refreshed: list[bool] = []
    binding = SessionBinding(
        conversation=conversation,
        abort=lambda: None,
        clear_messages=lambda: None,
        load_history=lambda messages: None,
        reset_task_monitor=lambda: None,
    )
    binding.configure(
        manager,
        active_session_id=lambda: "session-1",
        refresh_sessions=lambda: refreshed.append(True),
    )

    binding.auto_save({"thought": "A useful title"})
    assert manager.saved == [("session-1", conversation.messages)]
    assert manager.renamed == [("session-1", "A useful title")]
    assert refreshed == [True]

    binding.close()
    binding.close()
    binding.auto_save({"thought": "late"})
    assert len(manager.saved) == 1


class _TaskManager:
    def __init__(self) -> None:
        self.removed = []

    def get_status(self, task_id: str) -> dict:
        return {"task_id": task_id, "run_id": "run-1", "status": "completed"}

    def list_all(self) -> list[str]:
        return []

    def remove_listener(self, callback) -> None:
        self.removed.append(callback)


class _TaskConversation:
    def __init__(self) -> None:
        self.observations = []

    def add_observation(self, tool: str, message: str) -> None:
        self.observations.append((tool, message))


class _Controller:
    def __init__(self) -> None:
        self.completed = []

    def handle_task_completed(self, *args) -> None:
        self.completed.append(args)


def test_task_binding_deduplicates_terminal_event_and_ignores_late_after_close() -> None:
    parent = QWidget()
    conversation = _TaskConversation()
    controller = _Controller()
    messages = []
    binding = TaskBinding(
        parent=parent,
        conversation=conversation,
        system_message=messages.append,
        controller=lambda: controller,
        sanitize_error=lambda value: value,
    )
    manager = _TaskManager()
    binding._manager = manager

    binding._on_finished("task-1", True, "", {"success_count": 2})
    binding._on_finished("task-1", False, "late failure", None)
    assert len(controller.completed) == 1
    assert len(messages) == 1

    binding.close()
    binding.close()
    binding._on_finished("task-2", True, "", {})
    assert len(controller.completed) == 1
    assert manager.removed == [binding._on_finished, binding._on_updated]


class _RoundController:
    def __init__(self) -> None:
        self.events = []

    def handle_abort(self) -> None:
        self.events.append("abort")

    def handle_user_message(self, text: str) -> None:
        self.events.append(("message", text))

    def handle_llm_response(self, parsed: dict) -> None:
        self.events.append(("response", parsed))


class _MemoryStore:
    count = 0


class _StartableTaskBinding:
    def __init__(self) -> None:
        self.starts = 0

    def start(self) -> None:
        self.starts += 1


def test_conversation_binding_ignores_round_and_response_after_close() -> None:
    controller = _RoundController()
    tasks = _StartableTaskBinding()
    session = type("Session", (), {"auto_save": lambda self, parsed: None})()
    binding = ConversationBinding(
        memory_store=_MemoryStore(),
        memory_retriever=None,
        controller=lambda: controller,
        task_binding=lambda: tasks,
        session_binding=lambda: session,
        system_message=lambda message: None,
        observability_visible=lambda: False,
    )

    binding.start_round("hello")
    binding.handle_response({"type": "final"})
    binding.close()
    binding.close()
    binding.start_round("late")
    binding.handle_response({"type": "late"})

    assert tasks.starts == 1
    assert controller.events == [
        "abort",
        ("message", "hello"),
        ("response", {"type": "final"}),
    ]


def test_public_bubble_facades_delegate_to_message_list() -> None:
    bubbles = []
    message_list = type("MessageList", (), {"add_bubble": bubbles.append})()
    widget = ChatWidget.__new__(ChatWidget)
    QWidget.__init__(widget)
    widget._message_list = message_list

    widget.add_user_bubble("user text")
    widget.add_assistant_bubble("assistant text")

    assert [bubble._role for bubble in bubbles] == ["user", "assistant"]
    widget.close()


class _DisposableChat:
    def __init__(self) -> None:
        self.shutdown_calls = []

    def shutdown(self, *, wait_for_worker: bool) -> None:
        self.shutdown_calls.append(wait_for_worker)


class _Subscription:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_dock_close_hides_without_shutdown_and_dispose_is_idempotent() -> None:
    panel = SmartAssistantPanel.__new__(SmartAssistantPanel)
    QDockWidget.__init__(panel, "assistant")
    chat = _DisposableChat()
    subscription = _Subscription()
    panel._session_commands = None
    panel._active_session_id = None
    panel._chat = chat
    panel._session_subscription = subscription
    panel._disposed = False

    panel.show()
    panel.close()
    _APP.processEvents()

    assert chat.shutdown_calls == []
    assert subscription.close_count == 0
    panel.dispose(wait_for_worker=True)
    panel.dispose(wait_for_worker=False)
    assert chat.shutdown_calls == [True]
    assert subscription.close_count == 1


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _PanelStub:
    instances = []

    def __init__(self, *args, **kwargs) -> None:
        self.is_disposed = False
        self.visibility_changed = _Signal()
        self.destroyed = _Signal()
        self.deleted = False
        self.__class__.instances.append(self)

    def hide(self) -> None:
        pass

    def deleteLater(self) -> None:
        self.deleted = True

    def dispose(self, *, wait_for_worker: bool) -> None:
        self.is_disposed = True


def test_tool_windows_reuses_hidden_panel_but_recreates_disposed_panel(monkeypatch) -> None:
    _PanelStub.instances.clear()
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.SmartAssistantPanel",
        _PanelStub,
    )
    host = type(
        "Host",
        (),
        {
            "context": object(),
            "session_commands": None,
            "session_projection": None,
            "runtime_context": None,
            "addDockWidget": lambda self, area, panel: None,
        },
    )()
    windows = ToolWindows(host)

    first = windows.get_assistant_panel()
    assert windows.get_assistant_panel() is first
    first.is_disposed = True
    second = windows.get_assistant_panel()

    assert second is not first
    assert first.deleted is True
    assert len(_PanelStub.instances) == 2
