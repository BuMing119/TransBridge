"""Index and collection deliveries must leave the active translation draft intact."""

from PyQt6.QtGui import QInputMethodEvent, QTextCursor
from PyQt6.QtTest import QTest

from tests.dialogue_support import dialogue_entries
from tests.ui import test_dialogue_editor as editor_tests
from transbridge.application.dialogue.index import build_dialogue_index

editor = editor_tests.editor


def test_background_refresh_keeps_translation_focus_selection_and_undo(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    field = editor.view.translation
    QTest.keyClicks(field, "pending draft")
    cursor = field.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
    field.setTextCursor(cursor)

    editor.context.collection_changed.emit(editor.context.collection)
    editor._loaded(editor._generation, editor._scope, build_dialogue_index(dialogue_entries()))
    editor_tests._APP.processEvents()

    assert field.hasFocus()
    assert field.toPlainText() == "pending draft"
    assert field.textCursor().anchor() == 2
    assert field.textCursor().position() == 7
    field.undo()
    assert field.toPlainText() == ""


def test_background_refresh_keeps_uncommitted_input_method_composition(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    field = editor.view.translation
    editor_tests._APP.sendEvent(field, QInputMethodEvent("han", []))

    editor.context.collection_changed.emit(editor.context.collection)
    editor._loaded(editor._generation, editor._scope, build_dialogue_index(dialogue_entries()))
    editor_tests._APP.processEvents()

    assert field.hasFocus()
    assert field.textCursor().block().layout().preeditAreaText() == "han"
    commit = QInputMethodEvent()
    commit.setCommitString("寒")
    editor_tests._APP.sendEvent(field, commit)
    assert field.toPlainText() == "寒"


def test_plugin_reading_order_delivery_preserves_selected_entry_and_pending_draft(editor):
    entries = dialogue_entries()
    selected = entries[2]
    editor.open_entry(selected.identity)
    field = editor.view.translation
    QTest.keyClicks(field, "keep this draft")
    cursor = field.textCursor()
    cursor.setPosition(4)
    field.setTextCursor(cursor)
    before = [entry.to_dict() for entry in editor.context.collection]
    topic_orders = {(selected.identity.namespace.value, "00000001"): ("00000011", "00000010")}

    editor._loaded(editor._generation, editor._scope, build_dialogue_index(entries, topic_orders=topic_orders))
    editor_tests._APP.processEvents()

    assert editor._selected_key == selected.identity
    assert editor._current.before.entry_key == selected.identity
    assert editor._index.quests[0].topics[editor._topic].identity[2] == "00000010"
    assert editor._topic == 2
    assert field.hasFocus() and field.toPlainText() == "keep this draft"
    assert field.textCursor().position() == 4
    assert [entry.to_dict() for entry in editor.context.collection] == before
    field.undo()
    assert field.toPlainText() == ""
