"""IME composition and long navigation counts must not overlap editor hints."""

from PyQt6.QtGui import QInputMethodEvent

from tests.dialogue_support import dialogue_entries
from tests.ui import test_dialogue_editor as editor_tests

editor = editor_tests.editor


def test_empty_translation_composition_has_no_overlapping_hint_and_commits_normally(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    field = editor.view.translation
    assert field.toPlainText() == ""
    editor_tests._APP.sendEvent(field, QInputMethodEvent("han's", []))
    assert field.textCursor().block().layout().preeditAreaText() == "han's"
    assert field.placeholderText() == ""
    assert field.toPlainText() == ""
    commit = QInputMethodEvent()
    commit.setCommitString("寒霜")
    editor_tests._APP.sendEvent(field, commit)
    assert field.toPlainText() == "寒霜"
    assert editor._current.text == "寒霜"
    field.undo()
    assert field.toPlainText() == ""


def test_cancelled_composition_keeps_empty_translation_without_a_draft(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    field = editor.view.translation
    editor_tests._APP.sendEvent(field, QInputMethodEvent("han", []))
    editor_tests._APP.sendEvent(field, QInputMethodEvent())
    assert field.toPlainText() == ""
    assert not editor._drafts
    assert field.placeholderText() == ""


def test_four_digit_navigation_total_remains_on_one_line_at_minimum_window_size(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    editor.dialog.resize(820, 580)
    editor.view.show_navigation(1, 8291, False)
    editor_tests._APP.processEvents()
    label = editor.view.navigation_label
    assert label.text() == "打开时列表 · 2 / 8291"
    assert not label.wordWrap()
    assert label.width() >= label.sizeHint().width()
    assert label.geometry().right() < editor.view.discard_button.geometry().left()
