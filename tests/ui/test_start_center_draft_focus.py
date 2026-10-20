from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.coordinators.guided_project_coordinator import GuidedProjectCoordinator
from transbridge.ui.shell.start_center import StartCenterViewState, StartCenterWidget, StartDestinationState

_APP = QApplication.instance() or QApplication([])
_EDIT_FIELDS = [
    pytest.param("_name_edit", "project_name", id="project-name"),
    pytest.param("_variant_edit", "default_variant_name", id="variant-name"),
]


@pytest.fixture
def draft_form() -> Iterator[tuple[StartCenterWidget, GuidedProjectCoordinator]]:
    widget = StartCenterWidget()
    coordinator = GuidedProjectCoordinator(SimpleNamespace(), object(), on_state=widget.render_draft)
    widget.project_name_changed.connect(coordinator.set_project_name)
    widget.variant_name_changed.connect(coordinator.set_variant_name)
    coordinator.begin("D:/mods/MyMod.esp")
    widget.show()
    widget.activateWindow()
    _APP.processEvents()
    try:
        yield widget, coordinator
    finally:
        widget.close()
        widget.deleteLater()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()


def _type_at_focus(text: str) -> None:
    # Resolve the receiver for every keystroke, as real keyboard input does.
    for character in text:
        focused = _APP.focusWidget()
        assert focused is not None
        QTest.keyClicks(focused, character)
        _APP.processEvents()


@pytest.mark.parametrize(("edit_name", "state_field"), _EDIT_FIELDS)
def test_typing_stays_in_the_selected_draft_field_and_enter_submits(draft_form, edit_name, state_field) -> None:
    widget, coordinator = draft_form
    edit = getattr(widget, edit_name)
    original = edit.text()
    requested = []
    widget.prepare_requested.connect(lambda: requested.append(True))
    edit.setFocus()
    edit.setCursorPosition(len(original))

    _type_at_focus("-ABC")

    assert _APP.focusWidget() is edit
    assert edit.text() == original + "-ABC"
    assert getattr(coordinator.state, state_field) == edit.text()
    assert edit.text() in widget._draft_summary.text()
    assert edit.cursorPosition() == len(edit.text())
    assert requested == []
    QTest.keyClick(_APP.focusWidget(), Qt.Key.Key_Return)
    assert requested == [True]


@pytest.mark.parametrize(("edit_name", "state_field"), _EDIT_FIELDS)
def test_clearing_and_refilling_a_draft_field_keeps_focus(draft_form, edit_name, state_field) -> None:
    widget, coordinator = draft_form
    edit = getattr(widget, edit_name)
    other_edit = widget._variant_edit if edit is widget._name_edit else widget._name_edit
    other_text = other_edit.text()
    edit.setFocus()
    QTest.keyClick(_APP.focusWidget(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(_APP.focusWidget(), Qt.Key.Key_Backspace)
    _APP.processEvents()

    assert edit.text() == ""
    assert not widget._draft_primary.isEnabled()
    assert _APP.focusWidget() is edit

    _type_at_focus("Replacement")

    assert _APP.focusWidget() is edit
    assert edit.text() == "Replacement"
    assert getattr(coordinator.state, state_field) == "Replacement"
    assert other_edit.text() == other_text
    assert widget._draft_primary.isEnabled()


@pytest.mark.parametrize(("edit_name", "state_field"), _EDIT_FIELDS)
def test_draft_updates_preserve_insertion_position_and_selection(draft_form, edit_name, state_field) -> None:
    widget, coordinator = draft_form
    edit = getattr(widget, edit_name)
    original = edit.text()
    edit.setFocus()
    edit.setCursorPosition(1)

    _type_at_focus("AB")

    assert _APP.focusWidget() is edit
    assert edit.text() == original[:1] + "AB" + original[1:]
    assert edit.cursorPosition() == 3

    edit.setSelection(1, 2)
    coordinator.set_parse_option("skip_empty", True)
    _APP.processEvents()

    assert _APP.focusWidget() is edit
    assert edit.selectionStart() == 1
    assert edit.selectedText() == "AB"
    assert edit.cursorPosition() == 3

    _type_at_focus("CD")

    assert edit.text() == original[:1] + "CD" + original[1:]
    assert getattr(coordinator.state, state_field) == edit.text()
    assert edit.cursorPosition() == 3


def test_entering_a_valid_draft_focuses_the_primary_action(draft_form) -> None:
    widget, _coordinator = draft_form

    assert widget._draft_primary.isEnabled()
    assert _APP.focusWidget() is widget._draft_primary


def test_only_new_or_changed_diagnostics_take_focus_from_an_edit(draft_form) -> None:
    widget, coordinator = draft_form
    widget._variant_edit.setFocus()
    failed_state = replace(
        coordinator.state,
        revision=coordinator.state.revision + 1,
        diagnostic_code="PROJECT_NAME_CONFLICT",
        diagnostic_message="工程名称已存在。",
    )
    widget.render_draft(failed_state)
    _APP.processEvents()

    assert _APP.focusWidget() is widget._draft_error

    widget._name_edit.setFocus()
    widget._name_edit.setSelection(0, 2)
    widget.render_draft(replace(failed_state, revision=failed_state.revision + 1))
    _APP.processEvents()

    assert _APP.focusWidget() is widget._name_edit
    assert widget._name_edit.selectedText() == "My"

    widget.render_draft(
        replace(failed_state, revision=failed_state.revision + 2, diagnostic_message="请选择另一个工程名称。")
    )
    _APP.processEvents()

    assert _APP.focusWidget() is widget._draft_error


def test_reentering_a_draft_with_an_existing_diagnostic_focuses_the_error(draft_form) -> None:
    widget, coordinator = draft_form
    failed_state = replace(
        coordinator.state,
        revision=coordinator.state.revision + 1,
        diagnostic_code="PROJECT_NAME_CONFLICT",
        diagnostic_message="工程名称已存在。",
    )
    widget.render_draft(failed_state)
    widget._variant_edit.setFocus()
    widget.render(StartCenterViewState(StartDestinationState.START_CENTER_USER_REQUESTED, revision=1))
    widget.render_draft(failed_state)
    _APP.processEvents()

    assert _APP.focusWidget() is widget._draft_error
