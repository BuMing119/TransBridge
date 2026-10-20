"""Background conversation refreshes must not disturb ongoing text editing."""

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QInputMethodEvent, QTextCursor
from PyQt6.QtTest import QTest
import pytest

from tests.ui.tools.smart_assistant import test_v2_chat_recovery as fixtures
from tests.ui.tools.smart_assistant.test_v2_chat_recovery import _APP, _until
from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble

chat_environment = fixtures.chat_environment


def _shown_panel(environment):
    panel = environment.panel()
    _until(lambda: panel.chat.session_ready and panel._current_session_id() is not None)
    panel.resize(1100, 760)
    panel.show()
    panel.activateWindow()
    QTest.qWait(20)
    _APP.processEvents()
    return panel


def _draft(panel):
    editor = panel.chat._input
    editor.setPlainText("draft being edited")
    cursor = editor.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.setFocus()
    _APP.processEvents()
    assert editor.hasFocus()
    return editor, (cursor.position(), cursor.anchor())


def _changed_conversation(environment, panel, content="Background conversation update"):
    backend, controller = panel.chat.recovery_snapshot()
    messages = [*backend, {"role": "assistant", "content": content, "message_id": f"message-{len(backend)}"}]
    assert environment.commands.save_conversation(
        environment.lifecycle.active.aggregate.ref,
        messages,
        messages,
        RequestContext("owner"),
        controller=controller,
    ).is_success
    _APP.processEvents()


def test_same_session_projection_preserves_draft_selection_and_undo(chat_environment):
    panel = _shown_panel(chat_environment)
    editor, _ = _draft(panel)
    editor.insertPlainText("new")
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    expected_cursor = (cursor.position(), cursor.anchor())
    expected_text = editor.toPlainText()
    assert editor.document().isUndoAvailable()

    _changed_conversation(chat_environment, panel)

    assert editor.toPlainText() == expected_text
    assert editor.hasFocus()
    assert (editor.textCursor().position(), editor.textCursor().anchor()) == expected_cursor
    assert editor.document().isUndoAvailable()


def test_same_session_projection_keeps_history_search_focus(chat_environment):
    panel = _shown_panel(chat_environment)
    editor, _ = _draft(panel)
    navigation = panel.chat._message_list.navigation
    navigation.open_search()
    navigation.query.setText("conversation")
    navigation.query.setSelection(2, 4)
    _APP.processEvents()
    assert navigation.query.hasFocus()

    _changed_conversation(chat_environment, panel)

    assert editor.toPlainText() == "draft being edited"
    assert navigation.query.hasFocus()
    assert navigation.query.text() == "conversation"
    assert navigation.query.selectionStart() == 2
    assert navigation.query.selectedText() == "nver"
    assert navigation.search_panel.isVisible()


class _FocusEvents(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.focus_out = 0
        editor.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusOut:
            self.focus_out += 1
        return super().eventFilter(watched, event)


@pytest.mark.parametrize("target", ["composer", "history"])
def test_same_session_refresh_does_not_interrupt_input_method_commit(chat_environment, target):
    panel = _shown_panel(chat_environment)
    editor, _ = _draft(panel)
    if target == "history":
        navigation = panel.chat._message_list.navigation
        navigation.open_search()
        editor = navigation.query
        editor.setText("draft being edited")
        editor.setSelection(3, 6)
    _APP.processEvents()
    focus = _FocusEvents(editor)
    _APP.sendEvent(editor, QInputMethodEvent("zhongwen", []))

    _changed_conversation(chat_environment, panel)

    assert focus.focus_out == 0
    assert editor.hasFocus()
    commit = QInputMethodEvent()
    commit.setCommitString("中文")
    _APP.sendEvent(editor, commit)
    text = editor.toPlainText() if target == "composer" else editor.text()
    assert text == "dra中文ng edited"


def test_same_session_refresh_updates_search_hits_without_changing_search_editor(chat_environment):
    panel = _shown_panel(chat_environment)
    _changed_conversation(chat_environment, panel, "first matching answer")
    panel.chat._input.setFocus()
    navigation = panel.chat._message_list.navigation
    assert navigation.search("matching") == 1
    navigation.query.setSelection(1, 3)
    _APP.processEvents()
    assert navigation.query.hasFocus()
    original_match = navigation.records[navigation._hits[navigation._hit]]["content"]

    _changed_conversation(chat_environment, panel, "second matching answer")

    assert navigation.query.hasFocus()
    assert navigation.query.text() == "matching"
    assert navigation.query.selectedText() == "atc"
    assert navigation.search_panel.isVisible()
    assert len(navigation._hits) == 2
    assert navigation.records[navigation._hits[navigation._hit]]["content"] == original_match
    assert any(
        widget.text == "second matching answer"
        for widget in panel.chat._message_list._owned_widgets
        if isinstance(widget, MessageBubble)
    )
    navigation.next_hit()
    assert navigation.records[navigation._hits[navigation._hit]]["content"] == "second matching answer"
    navigation.close_search()
    assert panel.chat._input.hasFocus()


def test_same_session_refresh_can_add_the_first_search_match(chat_environment):
    panel = _shown_panel(chat_environment)
    navigation = panel.chat._message_list.navigation
    assert navigation.search("matching") == 0
    navigation.query.setSelection(1, 3)

    _changed_conversation(chat_environment, panel, "new matching answer")

    assert navigation.query.hasFocus()
    assert navigation.query.selectedText() == "atc"
    assert len(navigation._hits) == 1
    assert navigation.records[navigation._hits[0]]["content"] == "new matching answer"


@pytest.mark.parametrize("destination", ["latest", "earlier"])
def test_same_session_refresh_keeps_navigation_after_leaving_a_search_match(chat_environment, destination):
    panel = _shown_panel(chat_environment)
    message_list = panel.chat._message_list
    message_list._max_visible_widgets = 3
    navigation = message_list.navigation
    navigation.page_size = 3
    for index in range(12):
        _changed_conversation(chat_environment, panel, f"answer number {index}")
    assert navigation.search("number 11") == 1
    if destination == "latest":
        navigation.show_latest()
    else:
        navigation.show_earlier()
    active, start = navigation.active, navigation.start

    _changed_conversation(chat_environment, panel, "new answer")

    assert navigation.active is active
    assert navigation.start == start
    assert navigation.query.text() == "number 11"
    assert len(navigation._hits) == 1
    if active:
        assert message_list._page_widgets
        assert message_list._page_widgets[0].text == navigation.records[start]["content"]
    else:
        assert not message_list._page_widgets
        assert message_list._owned_widgets[-1].text == "new answer"


def test_presentation_stream_and_directory_refresh_preserve_composer_editing(chat_environment):
    panel = _shown_panel(chat_environment)
    editor, expected_cursor = _draft(panel)
    bubble = MessageBubble("begin", "assistant", theme=panel.chat._theme)
    panel.chat._message_list.add_bubble(bubble)

    for text in ("stream", "stream with more content", "stream with more content\nand a new line"):
        panel.chat._streaming_presenter.flush(text, bubble)
        panel.chat._presentation.refresh()
        panel._sessions._install_directory(chat_environment.commands.list_sessions())
        _APP.processEvents()

    assert editor.hasFocus()
    assert editor.toPlainText() == "draft being edited"
    assert (editor.textCursor().position(), editor.textCursor().anchor()) == expected_cursor


def test_session_search_rebuild_preserves_search_selection(chat_environment):
    panel = _shown_panel(chat_environment)
    sidebar = panel._session_list
    sidebar._toggle_search()
    sidebar._search_input.setText("conversation")
    sidebar._search_input.setSelection(2, 4)
    _APP.processEvents()
    assert sidebar._search_input.hasFocus()

    panel._sessions._install_directory(chat_environment.commands.list_sessions())
    _APP.processEvents()

    assert sidebar._search_input.hasFocus()
    assert sidebar._search_input.text() == "conversation"
    assert sidebar._search_input.selectionStart() == 2
    assert sidebar._search_input.selectedText() == "nver"
