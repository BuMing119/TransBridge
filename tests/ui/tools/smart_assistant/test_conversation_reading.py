"""Real Qt scrolling and bounded read-only history navigation."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _settle():
    for _ in range(8):
        QApplication.processEvents()


def test_new_messages_and_streaming_preserve_upward_reading(environment, monkeypatch):
    panel, chat = environment.panel, environment.panel.chat
    environment.binding.set_active(False)
    panel.resize(700, 620)
    panel.show()
    chat._message_list.clear()
    for i in range(15):
        chat.add_assistant_bubble(f"Message {i}: " + "readable history " * 70)
    _settle()
    view = chat._message_list
    bar = chat._scroll.verticalScrollBar()
    assert bar.maximum() > 500
    view.scroll_to_bottom()
    _settle()
    assert bar.value() == bar.maximum()
    bar.setValue(100)
    chat.add_assistant_bubble("A new reply")
    _settle()
    assert bar.value() == 100
    orch = chat._orchestrator
    monkeypatch.setattr(orch, "_is_current", lambda *_: True)
    orch._streaming_generation = 1
    orch._streaming_dirty = True
    orch._streaming_bubble = view._owned_widgets[-1]
    orch._streaming_text = "streamed text " * 90
    orch._flush_streaming()
    _settle()
    assert bar.value() == 100
    assert not chat._back_to_bottom_btn.isHidden()
    chat._back_to_bottom_btn.click()
    _settle()
    assert bar.value() == bar.maximum()
    chat.add_assistant_bubble("continue following " * 100)
    _settle()
    assert bar.value() == bar.maximum()
    orch._streaming_bubble = None
    orch._streaming_generation = None


def test_old_messages_remain_readable_without_unbounded_widgets(environment):
    chat = environment.panel.chat
    environment.binding.set_active(False)
    chat._message_list.clear()
    chat.load_history([{"role": "user", "content": f"Saved request {i}", "message_id": f"msg-{i}"} for i in range(135)])
    view = chat._message_list
    assert len(view._owned_widgets) == 100
    assert view._owned_widgets[0].text == "Saved request 35"
    assert view.navigation.earlier.isEnabled()
    view.navigation.earlier.click()
    assert view.navigation.active
    assert len(view._page_widgets) == 100
    assert view._page_widgets[0].text == "Saved request 0"
    assert view._page_widgets[-1].text == "Saved request 99"
    assert all(widget.isHidden() for widget in view._owned_widgets)
    assert sum(view._layout.indexOf(widget) >= 0 for widget in (*view._owned_widgets, *view._page_widgets)) == 100
    view.navigation.newer.click()
    assert not view.navigation.active and not view._page_widgets
    assert view._owned_widgets[0].text == "Saved request 35"
    view.navigation.earlier.click()
    assert view._page_widgets[0].text == "Saved request 0"
    chat._message_list.clear()
    assert not view.navigation.active and not view._page_widgets
    assert not view._history.records
    assert not view.navigation.earlier.isEnabled()


def test_long_answers_remain_selectable():
    bubble = MessageBubble("answer " * 1600, "assistant")
    try:
        assert bubble._content.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    finally:
        bubble.deleteLater()


def test_history_uses_complete_saved_answers_and_retains_local_notices(environment):
    from transbridge.ui.tools.smart_assistant.message_history_view import MessageHistory

    saved = [
        {"role": "user", "content": "question", "message_id": "u"},
        {"role": "assistant", "content": "complete answer", "message_id": "a"},
    ]
    history = MessageHistory(provider=lambda: saved)
    history.remember(saved[0])
    records = history.snapshot([
        {"role": "system", "content": "local status", "message_id": ""},
        {"role": "assistant", "content": "partial answer", "message_id": "a"},
    ])
    try:
        assert [record["content"] for record in records] == [
            "question",
            "local status",
            "complete answer",
        ]
    finally:
        history.clear()


def test_history_hides_routing_prose_but_keeps_committed_answers(environment):
    from transbridge.ui.tools.smart_assistant.message_history_view import MessageHistory

    routing = {
        "role": "assistant",
        "content": "internal routing prose",
        "message_id": "routing",
        "control_turn_id": "turn1",
        "tool_calls": [{"name": "submit_request_routing"}],
    }
    saved = [
        routing,
        {"role": "assistant", "content": "committed direct reply", "message_id": "reply"},
        {
            "role": "assistant",
            "content": "complete task answer",
            "message_id": "coverage",
            "control_turn_id": "turn2",
            "tool_calls": [{"name": "report_answer_coverage"}],
        },
    ]
    history = MessageHistory(provider=lambda: saved)
    records = history.snapshot([routing])
    try:
        assert [record["content"] for record in records] == [
            "committed direct reply",
            "complete task answer",
        ]
    finally:
        history.clear()
    environment.panel.chat._message_list.clear()
    environment.panel.chat.load_history(saved)
    assert [widget.text for widget in environment.panel.chat._message_list._owned_widgets] == [
        "committed direct reply",
        "complete task answer",
    ]


def test_pending_scroll_work_is_safe_when_its_widget_tree_is_destroyed():
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QPushButton, QScrollArea, QVBoxLayout, QWidget

    from transbridge.ui.tools.smart_assistant.message_list_view import MessageListView

    host = QWidget()
    scroll = QScrollArea(host)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.addStretch()
    scroll.setWidget(content)
    view = MessageListView(
        layout,
        scroll_area=scroll,
        back_to_bottom_button=QPushButton(host),
        timer_parent=host,
        max_visible_widgets=100,
    )
    view.add_bubble(MessageBubble("pending content", "assistant"))
    host.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle()
