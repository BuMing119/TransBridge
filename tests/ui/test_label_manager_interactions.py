"""Label editing, colour selection and cancellation preserve the user's draft."""

from copy import deepcopy
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QPushButton
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.workbench.labels_view import LabelManagerDialog


@pytest.fixture
def labels(layout_app):
    original = {"a": {"name": "角色", "color": "#2196F3"}, "b": {"name": "任务", "color": "#FF9800"}}
    dialog = LabelManagerDialog(original)
    dialog.show()
    settle_layout(layout_app)
    yield dialog, original
    dialog.close()
    dialog.deleteLater()
    settle_layout(layout_app)


def test_changing_colour_keeps_the_selected_label_and_name(labels, layout_app):
    dialog, original = labels
    before = deepcopy(original)
    dialog._list.setCurrentRow(0)
    dialog._color_buttons["#4CAF50"].click()
    settle_layout(layout_app)
    assert dialog._list.currentRow() == 0
    assert dialog._selected_id == "a"
    assert dialog._name_edit.text() == "角色"
    assert dialog._delete_button.isEnabled()
    assert dialog.get_label_library()["a"]["color"] == "#4CAF50"
    assert original == before


def test_name_edits_survive_selection_and_are_saved_only_in_the_detached_value(labels, layout_app):
    dialog, original = labels
    dialog._list.setCurrentRow(0)
    dialog._name_edit.setFocus()
    dialog._name_edit.selectAll()
    QTest.keyClicks(dialog._name_edit, "Characters")
    dialog._list.setCurrentRow(1)
    dialog._list.setCurrentRow(0)
    assert dialog._name_edit.text() == "Characters"
    assert dialog.get_label_library()["a"]["name"] == "Characters"
    dialog.reject()
    assert original["a"]["name"] == "角色"


def test_saving_a_new_name_adds_it_once_and_empty_existing_name_keeps_dialog_open(labels, layout_app):
    dialog, _original = labels
    dialog._name_edit.setFocus()
    QTest.keyClicks(dialog._name_edit, "New label")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert [value["name"] for value in dialog.get_label_library().values()].count("New label") == 1
    dialog.accept()
    assert [value["name"] for value in dialog.get_label_library().values()].count("New label") == 1

    dialog.show()
    dialog._list.setCurrentRow(0)
    dialog._name_edit.setFocus()
    dialog._name_edit.selectAll()
    QTest.keyClick(dialog._name_edit, Qt.Key.Key_Backspace)
    dialog.accept()
    assert dialog.isVisible()
    assert dialog._feedback.isVisible() and "名称" in dialog._feedback.text()


def test_new_draft_survives_browsing_and_is_added_once_on_save(labels, layout_app):
    dialog, original = labels
    QTest.keyClicks(dialog._name_edit, "Draft")
    dialog._color_buttons["#4CAF50"].click()
    dialog._list.setCurrentRow(0)
    dialog._list.setCurrentRow(1)
    dialog._new_button.click()
    assert dialog._name_edit.text() == "Draft"
    assert dialog._color_buttons["#4CAF50"].isChecked()
    dialog._list.setCurrentRow(0)
    dialog.accept()
    assert len(dialog.get_label_library()) == 3
    assert len(original) == 2
    assert {"name": "Draft", "color": "#4CAF50"} in dialog.get_label_library().values()


def test_add_then_edit_or_delete_never_changes_the_callers_library_on_cancel(labels, layout_app):
    dialog, original = labels
    before = deepcopy(original)
    QTest.keyClicks(dialog._name_edit, "Draft")
    dialog._add_button.click()
    assert dialog._name_edit.text() == "Draft"
    assert not dialog._add_button.isVisible()
    assert dialog._new_button.isEnabled()
    dialog._color_buttons["#4CAF50"].click()
    dialog._delete_button.click()
    assert len(dialog.get_label_library()) == 2
    assert dialog._name_edit.text() == ""
    assert dialog._add_button.isVisible()
    dialog.reject()
    assert original == before


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_colour_choices_show_their_actual_colour_and_survive_theme_changes(layout_app, mode):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    theme = ThemeView(service)
    dialog = LabelManagerDialog({"a": {"name": "角色", "color": "#2196F3"}}, theme_view=theme)
    dialog.show()
    dialog._list.setCurrentRow(0)
    try:
        for current in (mode, ThemeMode.DARK if mode is ThemeMode.LIGHT else ThemeMode.LIGHT):
            service.set_preference(ThemePreference(current, DEFAULT_THEME_ID), persist=False)
            settle_layout(layout_app)
            assert dialog._selected_id == "a"
            for colour, button in dialog._color_buttons.items():
                picture = button.grab().toImage()
                matching = sum(
                    picture.pixelColor(x, y).name().casefold() == colour.casefold()
                    for x in range(picture.width())
                    for y in range(picture.height())
                )
                assert matching > 30
                assert dialog.rect().contains(button.rect().translated(button.mapTo(dialog, button.rect().topLeft())))
            new = next(button for button in dialog.findChildren(QPushButton) if button.text() == "新建标签")
            assert new.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        settle_layout(layout_app)
        theme.close()
        service.close()
