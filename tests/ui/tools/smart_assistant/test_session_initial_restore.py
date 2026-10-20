"""First-session discovery must not discard text entered while it is loading."""

from threading import Event
from types import SimpleNamespace

from PyQt6.QtGui import QInputMethodEvent, QTextCursor
from PyQt6.QtTest import QTest
import pytest

from tests.ui.tools.smart_assistant import test_v2_chat_recovery as recovery

chat_environment = recovery.chat_environment


@pytest.fixture(params=["new", "saved"])
def pending_restore(chat_environment, monkeypatch, request):
    entered, release = Event(), Event()
    restore, backend = request.param, []
    if restore == "saved":
        ref, _visible, backend = recovery._seed(chat_environment)
        chat_environment.lifecycle.detach_session(ref)
        chat_environment.projection.rebuild(None)
    method = "list_sessions" if restore == "new" else "read_conversation"
    read = getattr(chat_environment.commands, method)

    def delayed_read(*args, **kwargs):
        entered.set()
        assert release.wait(10), "session read was not released"
        return read(*args, **kwargs)

    monkeypatch.setattr(chat_environment.commands, method, delayed_read)
    panel = chat_environment.panel()
    panel.show()
    panel.activateWindow()
    try:
        recovery._until(lambda: entered.is_set() and panel.chat.session_ready and panel.chat._input is not None)
        assert panel._current_session_id() is None
        assert not panel._workspace.ready()
        yield SimpleNamespace(panel=panel, release=release, backend=backend)
    finally:
        release.set()


def _finish_restore(case):
    case.release.set()
    recovery._until(lambda: case.panel._workspace.ready() and case.panel.chat.session_ready)
    assert case.panel.chat.recovery_snapshot()[0] == case.backend


@pytest.mark.parametrize("preedit", [False, True])
def test_first_session_delivery_preserves_input_started_during_loading(pending_restore, preedit):
    panel = pending_restore.panel
    editor = panel.chat._input
    editor.setFocus()
    QTest.keyClicks(editor, "Startup draft")
    cursor = editor.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    if preedit:
        recovery._APP.sendEvent(editor, QInputMethodEvent("zhong", []))
    assert panel.chat.send_user_message("Must not send before restoration") is False
    expected_text = editor.toPlainText()
    expected_cursor = editor.textCursor().position(), editor.textCursor().anchor()

    _finish_restore(pending_restore)

    assert editor.hasFocus()
    assert editor.toPlainText() == expected_text
    assert (editor.textCursor().position(), editor.textCursor().anchor()) == expected_cursor
    if preedit:
        assert editor.textCursor().block().layout().preeditAreaText() == "zhong"
        commit = QInputMethodEvent()
        commit.setCommitString("中")
        recovery._APP.sendEvent(editor, commit)
        assert editor.toPlainText() == "S中tup draft"
    else:
        assert editor.textCursor().selectedText() == "tar"
        editor.undo()
        assert editor.toPlainText() == ""


def test_first_session_delivery_preserves_history_search_input(pending_restore):
    navigation = pending_restore.panel.chat._message_list.navigation
    assert navigation.search("answer") == 0
    navigation.query.setSelection(1, 3)

    _finish_restore(pending_restore)

    assert navigation.query.hasFocus()
    assert navigation.search_panel.isVisible()
    assert navigation.query.text() == "answer"
    assert navigation.query.selectedText() == "nsw"
    matches = [navigation.records[index]["content"] for index in navigation._hits]
    assert matches == (["Backend answer", "Visible answer"] if pending_restore.backend else [])


def test_first_session_delivery_keeps_reference_files_prepared_during_loading(pending_restore, tmp_path):
    binding = pending_restore.panel.chat._upload_binding
    reference = tmp_path / "reference.txt"
    reference.write_text("Context prepared while the session loads", encoding="utf-8")
    binding.add_paths([reference])
    recovery._until(lambda: not binding.busy)
    expected = binding.snapshot()
    assert expected and expected[0]["content"] == reference.read_text(encoding="utf-8")

    _finish_restore(pending_restore)

    assert binding.snapshot() == expected


@pytest.mark.parametrize("action", ["create", "delete"])
def test_only_initial_binding_adopts_unowned_input(pending_restore, action):
    panel = pending_restore.panel
    panel.chat.set_input("First session draft")
    _finish_restore(pending_restore)
    initial_id = panel._current_session_id()

    if action == "create":
        panel._sessions.create("Other")
    else:
        panel._sessions.delete(initial_id)
    recovery._until(lambda: panel._workspace.ready() and panel._current_session_id() != initial_id)

    assert panel.chat._input.toPlainText() == ""
    if action == "create":
        panel._switch_to(initial_id)
        recovery._until(lambda: panel._workspace.ready() and panel._current_session_id() == initial_id)
        assert panel.chat._input.toPlainText() == "First session draft"
