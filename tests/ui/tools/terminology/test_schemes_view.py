from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from transbridge.ui.tools.terminology.schemes_controller import TerminologySchemesController
from transbridge.ui.tools.terminology.schemes_view import TerminologySchemesView
from transbridge.ui.workbench.terminology_profile_bar import (
    TerminologyProfileBarState,
    TerminologyProfileChoice,
)

_APP = QApplication.instance() or QApplication([])


class _Profiles(QObject):
    state_changed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.identity = ("project", "variant")
        self.state = TerminologyProfileBarState(
            choices=(TerminologyProfileChoice("classic", "经典译名"),),
            selected_profile_id="classic",
            enabled=True,
            can_manage=True,
            detail="正在使用“经典译名”。",
        )
        self.selected = []
        self.manager_opened = 0

    def select(self, profile_id) -> None:
        self.selected.append(profile_id)

    def open_manager(self) -> None:
        self.manager_opened += 1


def test_scheme_switcher_keeps_selection_visible_and_management_in_a_menu() -> None:
    profiles = _Profiles()
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), profiles, view)

    assert controller.parent() is view
    assert view.scheme_combo.currentData() == "classic"
    assert view.scheme_combo.currentText() == "经典译名"
    assert view.scheme_combo.accessibleName() == "当前术语副本"
    assert view.actions_button.text() == "选用术语源…"
    assert view.create_action.text() == "从术语源导入副本…"
    assert controller._imports._select_after_create
    assert view.actions_button.isEnabled()
    assert view.status_label.isHidden()
    assert view.scheme_combo.accessibleDescription() == "经典译名"

    view.scheme_combo.setCurrentIndex(0)
    view.manage_action.trigger()

    assert profiles.selected == [None]
    assert profiles.manager_opened == 1
    view.close()


def test_scheme_switcher_displays_none_when_no_library_is_selected() -> None:
    view = TerminologySchemesView()
    selections = []
    view.selection_requested.connect(selections.append)
    state = TerminologyProfileBarState(enabled=True, can_manage=True)
    view.render(state)

    assert view.scheme_combo.currentText() == "无"
    assert view.scheme_combo.currentData() is None
    assert view.scheme_combo.accessibleDescription() == "无"
    assert view.metadata.isHidden()
    assert view.metadata.text() == ""

    selected = _Profiles().state
    view.render(selected)
    assert view.scheme_combo.currentText() == "经典译名"
    view.render(replace(selected, selected_profile_id=None))
    assert view.scheme_combo.currentText() == "无"
    assert view.scheme_combo.count() == 2
    assert selections == []
    view.close()


def test_scheme_controller_passes_one_picker_selection_to_background_import(monkeypatch) -> None:
    profiles = _Profiles()
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), profiles, view)
    selection = SimpleNamespace(
        request="request",
        default_name="社区译名方案",
        reader_factory="reader-factory",
    )

    class _Picker:
        def __init__(self, _context, _parent) -> None:
            self.selection = selection

        @staticmethod
        def exec():
            from PyQt6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    captured = []
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.schemes_controller.TerminologySourcePickerDialog",
        _Picker,
    )
    controller._imports.start_with_reader = lambda request, **kwargs: captured.append((request, kwargs))

    controller.create_from_source()

    assert captured == [("request", {"default_name": "社区译名方案", "reader_factory": "reader-factory"})]
    view.close()
