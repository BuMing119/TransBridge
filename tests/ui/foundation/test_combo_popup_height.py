"""Exercise shared popup bounds on real selectors, including dynamically created ones."""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QComboBox, QInputDialog
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.paratranz.string_dialogs import _CreateStringDialog
from transbridge.ui.tools.terminology.term_edit_dialog import TermEditDialog
from transbridge.ui.workbench.terminology_profile_bar import (
    TerminologyProfileBar,
    TerminologyProfileBarState,
    TerminologyProfileChoice,
)


def _selector(kind, count):
    if kind == "file":
        owner = _CreateStringDialog([{"id": index, "name": f"File {index:03}"} for index in range(count)])
        return owner, owner._file_combo
    if kind == "profile":
        owner = TerminologyProfileBar()
        owner.render(
            TerminologyProfileBarState(
                choices=tuple(TerminologyProfileChoice(str(index), f"Profile {index:03}") for index in range(count)),
                enabled=True,
            )
        )
        return owner, owner.combo
    if kind == "plugin":
        owner = TermEditDialog(plugin_ids=[f"Plugin{index:03}.esp" for index in range(count)])
        owner.scope.setCurrentIndex(1)
        return owner, owner.plugin
    owner = QInputDialog()
    owner.setComboBoxItems([f"Snapshot {index:03}" for index in range(count)])
    owner.setComboBoxEditable(False)
    return owner, owner.findChild(QComboBox)


@pytest.mark.parametrize("kind", ["file", "profile", "plugin", "input_dialog"])
@pytest.mark.parametrize("count", [3, 78])
def test_shared_popup_bounds_keep_every_option_reachable(layout_app, kind, count):
    owner, combo = _selector(kind, count)
    owner.show()
    combo.setCurrentIndex(0)
    settle_layout(layout_app)
    size_before = owner.size()
    try:
        combo.showPopup()
        settle_layout(layout_app)
        view = combo.view()
        row_height = view.sizeHintForRow(0)
        assert row_height > 0
        assert view.viewport().height() <= row_height * min(combo.count(), 12)
        assert view.verticalScrollBar().isVisible() == (combo.count() > 12)
        assert owner.size() == size_before

        QTest.keyClick(view, Qt.Key.Key_End)
        settle_layout(layout_app)
        last = combo.model().index(combo.count() - 1, 0)
        assert view.viewport().rect().contains(view.visualRect(last).center())
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(last).center())
        settle_layout(layout_app)
        assert combo.currentIndex() == last.row()
        if kind == "file":
            assert owner.get_data()["file"] == count - 1
        elif kind == "plugin":
            combo.lineEdit().selectAll()
            QTest.keyClicks(combo.lineEdit(), "Custom.esp")
            assert combo.currentText() == "Custom.esp"
    finally:
        combo.hidePopup()
        # The term editor normally prompts to discard edited fields on close.
        owner.hide()
        owner.deleteLater()
        settle_layout(layout_app)


def test_existing_and_new_popups_stay_bounded_after_theme_switch(layout_app):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    existing = QComboBox()
    existing.addItems([f"Option {index:03}" for index in range(78)])
    existing.show()
    try:
        for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
            service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            created = QComboBox()
            created.addItems([f"Option {index:03}" for index in range(78)])
            created.show()
            try:
                for combo in (existing, created):
                    combo.showPopup()
                    settle_layout(layout_app)
                    view = combo.view()
                    assert view.viewport().height() <= view.sizeHintForRow(0) * 12
                    assert view.verticalScrollBar().isVisible()
                    combo.hidePopup()
            finally:
                created.hidePopup()
                created.close()
                created.deleteLater()
    finally:
        existing.hidePopup()
        existing.close()
        existing.deleteLater()
        service.close()
        settle_layout(layout_app)
