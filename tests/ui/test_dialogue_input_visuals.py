"""IME composition and long navigation counts must not overlap editor hints."""

from PyQt6.QtGui import QInputMethodEvent
import pytest

from tests.dialogue_support import dialogue_entries
from tests.ui import test_dialogue_editor as editor_tests
from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService

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


def test_editor_fields_keep_visible_labels_for_populated_and_empty_text(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    view = editor.view
    editor_tests._APP.processEvents()
    for label, field in ((view.original_label, view.original), (view.translation_label, view.translation)):
        assert label.isVisible()
        assert label.buddy() is field
        assert label.geometry().bottom() < field.geometry().top()
    assert view.translation.placeholderText() == ""
    assert view.apply_button.text() == "应用并返回"
    assert view.apply_next_button.property("tbSemanticState") == "primary"


def test_four_digit_navigation_total_remains_on_one_line_at_minimum_window_size(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    editor.dialog.resize(820, 580)
    editor.view.show_navigation(1, 8291, False)
    editor_tests._APP.processEvents()
    label = editor.view.navigation_label
    assert label.text() == "打开时列表 · 2 / 8291"
    assert not label.wordWrap()
    assert label.width() >= label.sizeHint().width()
    assert label.geometry().bottom() < editor.view.discard_button.geometry().top()


@pytest.mark.parametrize("point_size", [10, 14])
def test_narrow_editor_keeps_draft_status_and_actions_readable(layout_app, editor, point_size):
    from types import SimpleNamespace

    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    view = editor.view
    view.translation.setPlainText("本轮正在编辑的完整译文")
    view.show_navigation(1, 8291, False)
    try:
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            apply_layout_font(editor.dialog, service.snapshot(), point_size)
            editor.dialog.resize(820, 580)
            settle_layout(layout_app)
            assert view.translation.font().pointSizeF() == point_size
            assert editor.dialog.width() == 820
            assert view.draft_label.text() == "未应用草稿 1 条"
            assert view.draft_label.width() >= view.draft_label.sizeHint().width()
            assert view.navigation_label.width() >= view.navigation_label.sizeHint().width()
            assert view.translation.height() >= 60
            for button in (view.previous_button, view.next_button, view.discard_button, view.apply_next_button):
                assert view.draft_label.geometry().bottom() < button.geometry().top()
                assert view.editor_panel.rect().contains(button.geometry())
            assert view.translation.toPlainText() == "本轮正在编辑的完整译文"
        view.apply_next_button.click()
        assert editor.context.collection.get(target).translation == "本轮正在编辑的完整译文"
    finally:
        editor.discard()
        service.close()
