"""Real Qt history navigation preserves source identity, bounded views and live delivery."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QScrollArea, QVBoxLayout, QWidget

from tests.ui.tools.smart_assistant.test_conversation_reading import _settle
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.application.assistant_requests.attachments import encode_user_content
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.submission_scope import SubmissionScope
from transbridge.ui.tools.smart_assistant.theme_support import QUIET_BUTTON_STYLE

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _history(chat, count=335):
    records = [{"role": "assistant", "content": f"Saved answer {i}", "message_id": f"saved-{i}"} for i in range(count)]
    chat._message_list.clear()
    chat.load_history(records)
    return records


def test_multiple_inline_pages_return_to_latest_and_release_history_widgets(environment):
    environment.binding.set_active(False)
    chat = environment.panel.chat
    _history(chat)
    view = chat._message_list
    assert not view.navigation.isHidden()
    assert view.navigation.search_toggle.isHidden()
    assert view.navigation.search_panel.isHidden()
    assert view.navigation.status.isHidden()
    view.navigation.open_search()
    assert not view.navigation.search_panel.isHidden()
    view.navigation.close_search_button.click()
    assert view.navigation.search_panel.isHidden()
    assert view._owned_widgets[0].property("message_id") == "saved-235"
    for expected in (135, 35, 0):
        view.navigation.earlier.click()
        assert view._page_widgets[0].property("message_id") == f"saved-{expected}"
        assert len(view._page_widgets) <= 100 and len(view._owned_widgets) <= 100
        assert all(w.isHidden() for w in view._owned_widgets)
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert len(chat._msg_container.findChildren(MessageBubble)) <= 200
    assert not view.navigation.earlier.isEnabled()
    view.navigation.newer.click()
    assert view._page_widgets[0].property("message_id") == "saved-100"
    old_page = list(view._page_widgets)
    view.navigation.latest.click()
    assert not view._page_widgets and not view.navigation.active
    assert all(view._layout.indexOf(w) >= 0 for w in view._owned_widgets)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    from PyQt6 import sip

    assert all(sip.isdeleted(widget) for widget in old_page)
    assert len(chat._msg_container.findChildren(MessageBubble)) <= 100


def test_search_uses_complete_canonical_answers_and_positions_previous_next_hits(environment):
    environment.binding.set_active(False)
    chat, view = environment.panel.chat, environment.panel.chat._message_list
    saved = _history(chat)
    saved[3]["content"] = "complete " * 2000 + " uniquely findable tail"
    saved[208]["content"] = "Another UNIQUELY FINDABLE TAIL"
    view._history.provider = lambda: saved
    assert view.search_history("uniquely findable tail") == 2
    first = next(w for w in view._page_widgets if w.property("history_match"))
    assert first.property("message_id") == "saved-3"
    assert first.text == saved[3]["content"]
    assert "匹配 1/2" in view.navigation.status.text()
    assert not view.navigation.previous_match.isEnabled()
    view.navigation.next_match.click()
    assert next(w for w in view._page_widgets if w.property("history_match")).property("message_id") == "saved-208"
    assert "匹配 2/2" in view.navigation.status.text()
    view.navigation.previous_match.click()
    assert next(w for w in view._page_widgets if w.property("history_match")).property("message_id") == "saved-3"
    assert view.search_history("no such text") == 0
    assert "未找到" in view.navigation.status.text()
    assert not view.navigation.next_match.isEnabled()


def test_message_id_navigation_preserves_scope_attachment_summary_and_current_card_anchor(environment):
    environment.binding.set_active(False)
    chat, view = environment.panel.chat, environment.panel.chat._message_list
    saved = _history(chat)
    saved[0] = {
        "role": "user",
        "message_id": "canonical-user",
        "content": encode_user_content(
            "Read the guide", ({"name": "guide.txt", "format": "text", "content": "raw reference"},)
        ),
    }
    view._history.provider = lambda: saved
    view.set_scopes({"canonical-user": SubmissionScope("Original project · 2 entries", "Entry A and B")})
    card = QWidget(chat)
    view.attach(card, ("canonical-user",))
    assert card.isHidden()
    assert view.jump_to_message("canonical-user")
    target = next(w for w in view._page_widgets if w.property("message_id") == "canonical-user")
    assert target.text == "Read the guide\n\n本次附件：guide.txt"
    assert target._scope_view.summary.text() == "Original project · 2 entries"
    assert not card.isHidden() and view._layout.indexOf(card) > view._layout.indexOf(target)
    assert not view.jump_to_message("missing-id")
    assert "不在当前会话" in view.navigation.status.text()
    view.navigation.latest.click()
    assert card.isHidden()
    view.detach(card)
    card.deleteLater()


def test_new_streams_do_not_replace_old_page_or_move_its_scroll_position(environment):
    environment.binding.set_active(False)
    panel, chat = environment.panel, environment.panel.chat
    panel.resize(760, 800)
    panel.show()
    _history(chat)
    view = chat._message_list
    view.navigation.earlier.click()
    _settle()
    bar = chat._scroll.verticalScrollBar()
    bar.setValue(100)
    anchor = view._page_widgets[0]
    live = MessageBubble("Starting", "assistant")
    view.add_bubble(live)
    chat._streaming_presenter.flush("complete incoming text " * 100, live)
    view.follow_tail()
    _settle()
    assert view._page_widgets[0] is anchor
    assert bar.value() == 100
    assert live.isHidden() and view.contains(live)
    assert "1 条新消息" in view.navigation.latest.text()
    live.copy_button.click()
    assert QApplication.clipboard().text() == "complete incoming text " * 100
    view.navigation.latest.click()
    _settle()
    assert not live.isHidden()
    assert bar.value() == bar.maximum()


def test_historical_tool_messages_never_recreate_execution_controls(environment):
    environment.binding.set_active(False)
    chat, view = environment.panel.chat, environment.panel.chat._message_list
    saved = _history(chat)
    saved[0] = {
        "role": "assistant",
        "content": "Review old proposal",
        "message_id": "proposal",
        "tool_calls": [{"name": "translate", "arguments": {"text": "old"}}],
    }
    saved.insert(
        1,
        {
            "role": "tool",
            "content": "raw internal payload",
            "display_summary": "Previously cancelled",
            "message_id": "receipt",
        },
    )
    saved.insert(
        0,
        {"role": "assistant", "content": "secret routing prose", "message_id": "routing", "control_batch_id": "batch"},
    )
    view._history.provider = lambda: saved
    assert view.search_history("secret routing prose") == 0
    assert view.jump_to_message("proposal")
    assert view.search_history("Previously cancelled") == 1
    controls = [button.text() for widget in view._page_widgets for button in widget.findChildren(QPushButton)]
    assert not any("执行" in text or "确认" in text or "重试" in text for text in controls)


def test_copy_uses_full_markdown_and_clear_invalidates_page_search(environment):
    environment.binding.set_active(False)
    chat, view = environment.panel.chat, environment.panel.chat._message_list
    text = "# Heading\n\n```python\nprint('copied')\n```\n\n" + "完整正文 " * 3000
    bubble = MessageBubble(text, "assistant")
    view.add_bubble(bubble)
    bubble.copy_button.click()
    assert QApplication.clipboard().text() == text
    assert bubble.copy_button.text() == "已复制"
    assert view.search_history("完整正文") == 1
    chat._message_list.clear()
    _settle()
    assert not view.navigation.active and not view._page_widgets
    assert view.navigation.query.text() == ""
    assert not view.navigation.next_match.isEnabled()
    assert not view._history.records


def test_queued_history_location_is_safe_after_parent_destruction():
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
    bubble = MessageBubble("historical text", "assistant")
    bubble.setProperty("message_id", "stable-id")
    view.add_bubble(bubble)
    assert view.jump_to_message("stable-id")
    host.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle()


def test_switching_sessions_resets_search_page_and_original_message_identity(environment):
    panel, chat = environment.panel, environment.panel.chat
    environment.binding.set_active(False)
    first_session = panel._current_session_id()
    chat._conversation.add_user("Unique message from previous session", message_id="previous-user")
    chat.load_history(chat._conversation.get_transcript())
    assert chat._message_list.search_history("Unique message") == 1
    assert chat._message_list.navigation.active
    panel._on_create_session("Next session")
    _until(lambda: panel._current_session_id() != first_session and not chat._message_list.navigation.active)
    assert chat._message_list.navigation.query.text() == ""
    assert not chat._message_list._page_widgets
    assert chat._message_list.search_history("Unique message") == 0
    assert not chat._message_list.jump_to_message("previous-user")


def test_history_read_failure_preserves_current_page_and_can_be_retried(environment):
    environment.binding.set_active(False)
    chat, view = environment.panel.chat, environment.panel.chat._message_list
    saved = _history(chat)
    assert view.jump_to_message("saved-3")
    original = tuple(view._page_widgets)

    def unavailable():
        raise RuntimeError("injected history read error")

    view._history.provider = unavailable
    assert view.search_history("Saved answer") == 0
    assert "无法读取" in view.navigation.status.text()
    assert tuple(view._page_widgets) == original
    view._history.provider = lambda: saved
    assert view.search_history("Saved answer 3") > 0
    assert "匹配" in view.navigation.status.text()


def test_short_conversation_has_no_history_chrome_and_search_close_restores_focus(environment):
    environment.binding.set_active(False)
    panel, chat = environment.panel, environment.panel.chat
    panel.show()
    panel.activateWindow()
    _history(chat, 5)
    nav = chat._message_list.navigation
    _settle()
    assert nav.isHidden() and nav.search_toggle.isHidden()
    chat._input.setFocus()
    nav.open_search()
    _settle()
    assert not nav.isHidden() and nav.query.hasFocus()
    assert nav._pages.isHidden()
    nav.close_search_button.click()
    _settle()
    assert nav.isHidden() and chat._input.hasFocus()
    nav.open_search()
    QTest.keyClick(nav.query, Qt.Key.Key_Escape)
    _settle()
    assert nav.isHidden() and chat._input.hasFocus()
    assert panel.isVisible()
    chat.add_assistant_bubble("new message")
    assert nav.isHidden()


def test_escape_closes_search_without_replacing_history_or_stealing_new_message_scroll(environment):
    environment.binding.set_active(False)
    panel, chat = environment.panel, environment.panel.chat
    panel.resize(760, 800)
    panel.show()
    panel.activateWindow()
    _history(chat)
    view, nav = chat._message_list, chat._message_list.navigation
    chat._input.setFocus()
    nav.open_search()
    assert nav.search("Saved answer 15") > 0
    _settle()
    bar = chat._scroll.verticalScrollBar()
    bar.setValue(100)
    page = tuple(view._page_widgets)
    QTest.keyClick(nav.query, Qt.Key.Key_Escape)
    _settle()
    assert nav.active and not nav.isHidden()
    assert nav.search_panel.isHidden()
    assert tuple(view._page_widgets) == page
    assert bar.value() == 100
    chat.add_assistant_bubble("new incoming answer")
    _settle()
    assert tuple(view._page_widgets) == page and bar.value() == 100
    assert "1 条新消息" in nav.latest.text()
    nav.open_search()
    assert nav.query.text() == "Saved answer 15"
    nav.close_search()
    nav.latest.click()
    assert not nav.active and not view._page_widgets


def test_narrow_window_search_has_readable_query_and_styled_accessible_icon_actions(environment):
    environment.binding.set_active(False)
    panel, chat = environment.panel, environment.panel.chat
    panel.resize(480, 620)
    panel.show()
    _history(chat, 5)
    nav = chat._message_list.navigation
    nav.open_search()
    _settle()
    assert panel.width() == 480
    assert nav.query.width() >= 160
    assert nav.earlier.styleSheet() == QUIET_BUTTON_STYLE  # Initial composition applies the theme.
    for button in (nav.find, nav.previous_match, nav.next_match, nav.close_search_button):
        assert button.text() == "" and not button.icon().isNull()
        assert 28 <= button.width() <= 30 and 28 <= button.height() <= 30
        assert button.accessibleName() and button.toolTip()
        assert button.styleSheet() == QUIET_BUTTON_STYLE
        assert nav.search_panel.rect().contains(button.geometry())
    nav.query.setText("Saved answer")
    nav.find.click()
    assert "匹配 1/5" in nav.status.text()
    nav.next_match.click()
    assert "匹配 2/5" in nav.status.text()
    nav.previous_match.click()
    assert "匹配 1/5" in nav.status.text()
    nav.close_search_button.click()
    assert nav.search_panel.isHidden()
