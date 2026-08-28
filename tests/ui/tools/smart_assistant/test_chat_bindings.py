from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.shell.overlay_geometry import workspace_overlay_rect
from transbridge.ui.shell.tool_windows import ToolWindows
from transbridge.ui.tools.smart_assistant import panel as panel_module
from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.message_list_view import MessageListView
from transbridge.ui.tools.smart_assistant.panel import SmartAssistantPanel
from transbridge.ui.tools.smart_assistant.session_binding import ConversationBinding, SessionBinding
from transbridge.ui.tools.smart_assistant.streaming_presenter import StreamingPresenter
from transbridge.ui.tools.smart_assistant.task_binding import TaskBinding

_APP = QApplication.instance() or QApplication([])


def test_assistant_overlay_rect_caps_large_hosts_and_fits_small_hosts() -> None:
    large = workspace_overlay_rect(QRect(0, 0, 2560, 1440))
    small = workspace_overlay_rect(QRect(0, 0, 800, 500))

    assert (large.width(), large.height()) == (1280, 820)
    assert large.center() == QRect(0, 0, 2560, 1440).center()
    assert (small.width(), small.height()) == (752, 452)
    assert small.left() == 24
    assert small.top() == 24


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

    def setWindowIcon(self, icon) -> None:
        self.window_icon = icon

    def deleteLater(self) -> None:
        self.deleted = True

    def dispose(self, *, wait_for_worker: bool) -> None:
        self.is_disposed = True


class _ResponsiveChatStub(QWidget):
    def __init__(self, ctx, parent=None, *, theme=None) -> None:
        super().__init__(parent)
        self.context = ctx

    def configure_session_port(self, **_kwargs) -> None:
        pass

    def set_session_manager(self, _manager) -> None:
        pass

    def set_task_monitor(self, _monitor) -> None:
        pass

    def shutdown(self, *, wait_for_worker: bool) -> None:
        pass


class _PanelSessionManager:
    def list_sessions(self) -> list[dict]:
        return []


def test_smart_assistant_panel_uses_responsive_dock_constraints(monkeypatch) -> None:
    taskbar_identities = []
    cleared_taskbar_identities = []
    monkeypatch.setattr(panel_module, "ChatWidget", _ResponsiveChatStub)
    monkeypatch.setattr(
        panel_module,
        "set_window_app_user_model_id",
        lambda panel, app_id: taskbar_identities.append((panel, app_id)) or True,
    )
    monkeypatch.setattr(
        panel_module,
        "clear_window_app_user_model_id",
        lambda panel: cleared_taskbar_identities.append(panel) or True,
    )
    monkeypatch.setattr(SmartAssistantPanel, "_init_skills", lambda self: None)
    monkeypatch.setattr(
        SmartAssistantPanel,
        "_init_session_manager",
        lambda self: setattr(self, "_session_mgr", _PanelSessionManager()),
    )
    monkeypatch.setattr(SmartAssistantPanel, "_restore_last_session", lambda self: None)
    monkeypatch.setattr(SmartAssistantPanel, "_configured_model_name", staticmethod(lambda: "test-model"))

    panel = SmartAssistantPanel(object())
    container = panel.widget()
    horizontal_splitter = container.layout().itemAt(0).widget()
    right_splitter = horizontal_splitter.widget(1)

    assert panel.minimumWidth() == 400
    assert panel.minimumHeight() == 220
    assert panel.parent() is None
    assert panel.windowFlags() & Qt.WindowType.Window
    assert panel.allowedAreas() == Qt.DockWidgetArea.NoDockWidgetArea
    assert panel.features() == QDockWidget.DockWidgetFeature.DockWidgetClosable
    assert container.minimumWidth() == 0
    assert container.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Ignored
    assert panel._task_monitor.minimumHeight() == 36
    assert panel._task_monitor.maximumHeight() == 36
    assert panel._task_monitor._collapsed is True
    assert panel.objectName() == "SmartAssistantPanel"
    assert "border: none" in panel.styleSheet()
    assert panel.titleBarWidget().accessibleName() == "智能助手标题栏"
    assert "border: 2px solid palette(text)" in panel.titleBarWidget().styleSheet()
    assert container.objectName() == "smartAssistantBody"
    assert "border-left: 2px solid palette(text)" in container.styleSheet()
    assert "border-bottom: 2px solid palette(text)" in container.styleSheet()
    assert panel.titleBarWidget()._model._value.full_text == "test-model"
    assert "已配置" in panel.titleBarWidget()._status.text()
    assert panel.titleBarWidget()._minimize_button.accessibleName() == "最小化智能助手"
    assert not panel.titleBarWidget()._minimize_button.icon().isNull()
    assert right_splitter.sizes()[0] > right_splitter.sizes()[1]
    assert panel.isVisible() is False
    assert taskbar_identities == [(panel, "TransBridge.SmartAssistant")]
    panel.setFloating(True)
    panel.show()
    _APP.processEvents()
    panel.hide()
    panel.show()
    panel.titleBarWidget()._minimize_button.click()
    _APP.processEvents()
    assert panel.isMinimized()
    assert taskbar_identities == [(panel, "TransBridge.SmartAssistant")]
    panel.showNormal()
    panel.dispose(wait_for_worker=False)
    assert cleared_taskbar_identities == [panel]


class _OverlayPanelStub(QDockWidget):
    instances = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("assistant", args[1])
        self.is_disposed = False
        self.visibility_changed = _Signal()
        self.setMinimumSize(400, 220)
        self.__class__.instances.append(self)

    def dispose(self, *, wait_for_worker: bool) -> None:
        self.is_disposed = True


def test_tool_windows_shows_assistant_as_centered_workspace_without_resizing_central_widget(monkeypatch) -> None:
    _OverlayPanelStub.instances.clear()
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.SmartAssistantPanel",
        _OverlayPanelStub,
    )
    host = QMainWindow()
    host.context = object()
    host.session_commands = None
    host.session_projection = None
    host.runtime_context = None
    central = QWidget()
    host.setCentralWidget(central)
    host.resize(1280, 720)
    host.show()
    _APP.processEvents()
    host_size = host.size()
    central_geometry = central.geometry()
    windows = ToolWindows(host)

    windows.toggle_smart_assistant()
    _APP.processEvents()
    panel = windows.assistant_panel
    first_overlay_geometry = panel.geometry()

    assert panel.isFloating()
    assert panel.parent() is None
    assert panel.windowFlags() & Qt.WindowType.Window
    assert host.size() == host_size
    assert central.geometry() == central_geometry
    assert panel.size() == workspace_overlay_rect(host.rect()).size()
    assert panel.width() == 998
    assert panel.height() == 600
    assert panel.frameGeometry().top() >= host.mapToGlobal(host.rect().topLeft()).y()
    assert panel.frameGeometry().bottom() <= host.mapToGlobal(host.rect().bottomRight()).y()

    panel.showMinimized()
    _APP.processEvents()
    assert panel.isMinimized()
    windows.toggle_smart_assistant()
    _APP.processEvents()
    assert panel.isVisible()
    assert not panel.isMinimized()

    windows.toggle_smart_assistant()
    windows.toggle_smart_assistant()
    _APP.processEvents()

    assert panel.isVisible()
    assert panel.isFloating()
    assert panel.geometry() == first_overlay_geometry
    assert central.geometry() == central_geometry

    windows.toggle_smart_assistant()
    host.resize(1600, 900)
    _APP.processEvents()
    resized_central_geometry = central.geometry()
    windows.toggle_smart_assistant()
    _APP.processEvents()

    assert panel.size() == workspace_overlay_rect(host.rect()).size()
    assert panel.width() == 1248
    assert panel.height() == 738
    assert central.geometry() == resized_central_geometry
    windows.dispose(wait_for_worker=False)
    _APP.processEvents()
    assert panel.is_disposed is True
    assert windows.assistant_panel is None
    host.close()


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
