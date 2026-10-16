"""Quiet message actions stay reachable without moving the text around."""

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget
import pytest

from tests.ui.tools.smart_assistant.test_conversation_reading import _settle
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.submission_scope import SubmissionScope

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


@pytest.fixture
def message_window():
    # Prior integration fixtures queue top-level deletion. Drain it before Qt's
    # offscreen mouse router establishes the next window's enter/leave target.
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle()
    host = QWidget()
    layout = QVBoxLayout(host)
    before = QLineEdit()
    bubble = MessageBubble("# Complete reply\n\n**Keep the entire Markdown text.**", "assistant")
    after = QLineEdit()
    for widget in (before, bubble, after):
        layout.addWidget(widget)
    host.resize(650, 400)
    host.show()
    host.raise_()
    host.activateWindow()
    assert QTest.qWaitForWindowExposed(host)
    _settle()
    before.setFocus()
    QTest.mouseMove(before, before.rect().center())
    _settle()
    try:
        yield host, before, bubble, after
    finally:
        host.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _settle()


def test_copy_appears_on_hover_without_changing_message_geometry(message_window):
    _, before, bubble, _ = message_window
    assert not bubble.copy_button.revealed
    assert not bubble.copy_button.isHidden()  # Paint is quiet; the keyboard target still exists.
    assert bubble._role_label.isHidden()
    assert bubble.accessibleName() == "助手消息"
    content_top = bubble._content_wrapper.mapTo(bubble, QPoint(0, 0)).y()
    assert bubble._avatar.mapTo(bubble, QPoint(0, 0)).y() == content_top
    assert bubble.copy_button.mapTo(bubble, QPoint(0, 0)).y() >= content_top + bubble._content_wrapper.height()
    geometry = (bubble.geometry(), bubble._content_wrapper.geometry(), bubble.copy_button.geometry())
    QTest.mouseMove(bubble, QPoint(5, 5))
    _settle()
    assert bubble.copy_button.revealed
    assert geometry == (bubble.geometry(), bubble._content_wrapper.geometry(), bubble.copy_button.geometry())
    QTest.mouseMove(before, before.rect().center())
    _settle()
    assert not bubble.copy_button.revealed
    assert geometry == (bubble.geometry(), bubble._content_wrapper.geometry(), bubble.copy_button.geometry())


def test_copy_is_in_tab_order_and_copies_complete_streamed_text_without_layout_jump(message_window):
    _, before, bubble, after = message_window
    geometry = (bubble.geometry(), bubble._content_wrapper.geometry(), bubble.copy_button.geometry())
    QTest.keyClick(before, Qt.Key.Key_Tab)
    _settle()
    assert bubble.copy_button.hasFocus() and bubble.copy_button.revealed
    text = "# Final stream\n\n" + "complete source " * 2000
    bubble.update_stream_text(text)
    QTest.keyClick(bubble.copy_button, Qt.Key.Key_Space)
    _settle()
    assert QApplication.clipboard().text() == text
    assert bubble.copy_button.text() == "已复制"
    assert geometry == (bubble.geometry(), bubble._content_wrapper.geometry(), bubble.copy_button.geometry())
    after.setFocus()
    _settle()
    assert not bubble.copy_button.revealed


def test_focused_message_content_reveals_copy_and_location_stays_accessible(message_window):
    _, _, bubble, after = message_window
    bubble._content.setFocus(Qt.FocusReason.TabFocusReason)
    _settle()
    assert bubble.copy_button.revealed
    bubble.set_history_highlight(True)
    assert not bubble._location_label.isHidden()
    assert bubble._location_label.text() == "已定位此消息"
    after.setFocus()
    _settle()
    assert not bubble.copy_button.revealed


def test_submitted_scope_shares_action_row_and_destroyed_message_disconnects_focus(message_window):
    _, before, bubble, after = message_window
    bubble.set_scope(SubmissionScope("Original project · 2 entries", "Original entry A and B"))
    _settle()
    assert bubble._scope_view.summary.isHidden()
    assert bubble._scope_view.details_button.text() == "提交范围"
    scope_button = bubble._scope_view.details_button
    assert scope_button.mapTo(bubble, QPoint(0, 0)).y() == bubble.copy_button.mapTo(bubble, QPoint(0, 0)).y()
    content_bottom = bubble._content_wrapper.mapTo(bubble, bubble._content_wrapper.rect().bottomLeft()).y()
    assert scope_button.mapTo(bubble, QPoint(0, 0)).y() > content_bottom
    assert "Original project" in scope_button.toolTip()
    before.setFocus()
    QTest.keyClick(before, Qt.Key.Key_Tab)
    assert scope_button.hasFocus()
    bubble.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    after.setFocus()
    _settle()
