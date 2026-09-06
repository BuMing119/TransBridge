from __future__ import annotations

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


def test_scheme_page_is_the_primary_place_to_switch_create_and_manage() -> None:
    profiles = _Profiles()
    view = TerminologySchemesView()
    controller = TerminologySchemesController(view, SimpleNamespace(), profiles, view)

    assert controller.parent() is view
    assert view.scheme_combo.currentData() == "classic"
    assert view.create_button.text() == "从术语来源创建…"
    assert view.create_button.isEnabled()
    assert "经典译名" in view.status_label.text()

    view.scheme_combo.setCurrentIndex(0)
    view.manage_button.click()

    assert profiles.selected == [None]
    assert profiles.manager_opened == 1
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
