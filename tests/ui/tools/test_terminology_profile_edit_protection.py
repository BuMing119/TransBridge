from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QListWidget, QMessageBox
import pytest

from tests.ui.workbench.terminology_async_support import wait_profile
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileState,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.ui.tools.terminology_profiles import TerminologyProfileManagerDialog, manager_dialog as manager_module
from transbridge.ui.tools.terminology_profiles.edit_prompts import confirm_archive
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def editor(monkeypatch):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    first = service.create_with_content("project-a", "A", _content("saved"))
    second = service.create_with_content("project-a", "B", _content("second"))
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    dialog = TerminologyProfileManagerDialog(service, "project-a", selected_profile_id=second.profile_id)
    yield service, dialog, first, second
    dialog.close()
    _APP.processEvents()


def _content(target):
    return TerminologyProfileContent(mappings=(ProfileTermMapping("Dragon", target, "龙"),))


def test_current_profile_and_rename_preserve_edits_and_revision(editor, monkeypatch):
    service, dialog, _first, second = editor
    assert dialog.current_profile.profile_id == second.profile_id
    assert dialog.mapping_table.model().setData(dialog.mapping_table.model().index(0, 2), "local edit")
    # A harmless external revision update can be adopted when content did not change.
    service.save_draft(second.profile_id, second.draft, expected_revision=0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed", True))
    dialog._rename_profile()
    assert dialog.mapping_table.model().index(0, 2).data() == "local edit"
    assert dialog.mapping_table.is_dirty
    assert dialog.current_profile.name == "Renamed"
    assert dialog.current_profile.draft_revision == 1
    assert dialog._save_draft()
    saved = next(item for item in service.list_profiles("project-a") if item.profile_id == second.profile_id)
    assert saved.draft_revision == 2
    assert saved.draft.mappings[0].translation == "local edit"


def test_rename_cannot_hide_concurrent_content_change(editor, monkeypatch):
    service, dialog, _first, second = editor
    assert dialog.mapping_table.model().setData(dialog.mapping_table.model().index(0, 2), "local edit")
    service.save_draft(second.profile_id, _content("external edit"), expected_revision=0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed", True))
    dialog._rename_profile()
    assert not dialog._save_draft()
    assert dialog.mapping_table.model().index(0, 2).data() == "local edit"
    assert "操作失败" in dialog.status_label.text()
    saved = next(item for item in service.list_profiles("project-a") if item.profile_id == second.profile_id)
    assert saved.draft.mappings[0].translation == "external edit"


@pytest.mark.parametrize(
    "choice", [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel]
)
def test_switch_respects_save_discard_cancel(editor, monkeypatch, choice):
    service, dialog, first, second = editor
    assert dialog.mapping_table.model().setData(dialog.mapping_table.model().index(0, 2), "local edit")
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: choice)
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData(first.profile_id))
    saved = next(item for item in service.list_profiles("project-a") if item.profile_id == second.profile_id)
    assert saved.draft.mappings[0].translation == (
        "local edit" if choice == QMessageBox.StandardButton.Save else "second"
    )
    if choice == QMessageBox.StandardButton.Cancel:
        assert dialog.current_profile.profile_id == second.profile_id
        assert dialog.profile_combo.currentData() == second.profile_id
        assert dialog.mapping_table.model().index(0, 2).data() == "local edit"
    else:
        assert dialog.current_profile.profile_id == first.profile_id
        assert not dialog.mapping_table.is_dirty
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)


def test_incomplete_row_is_protected_and_failed_save_cannot_leave(editor, monkeypatch):
    _service, dialog, first, second = editor
    dialog.mapping_table.append_mapping()
    assert dialog.mapping_table.is_dirty
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Save)
    assert not dialog.select_profile(first.profile_id)
    assert dialog.current_profile.profile_id == second.profile_id
    assert dialog.mapping_table.model().rowCount() == 2
    assert "操作失败" in dialog.status_label.text()
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Cancel)
    dialog.show_archived.setChecked(True)
    assert not dialog.show_archived.isChecked()
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)


@pytest.mark.parametrize("close_action", ["close", "escape"])
def test_close_and_escape_can_cancel_without_losing_input(editor, monkeypatch, close_action):
    _service, dialog, _first, _second = editor
    dialog.show()
    assert dialog.mapping_table.model().setData(dialog.mapping_table.model().index(0, 2), "local edit")
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Cancel)
    if close_action == "close":
        dialog.close()
    else:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible()
    assert dialog.mapping_table.model().index(0, 2).data() == "local edit"
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)


def test_archive_reviews_actual_users_and_restore_does_not_rebind(editor, monkeypatch):
    service, dialog, first, second = editor
    for profile in (first, second):
        service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project-a", "variant-a", second.profile_id)
    service.select("project-a", "variant-b", first.profile_id)
    observed = []
    monkeypatch.setattr(
        manager_module, "confirm_archive", lambda _parent, _name, users, _labels: observed.append(users) or True
    )
    dialog._archive_profile()
    assert [item.variant_id for item in observed[0]] == ["variant-a"]
    assert service.selected_revision("project-a", "variant-a") is None
    assert service.selected_revision("project-a", "variant-b").profile_id == first.profile_id
    dialog.show_archived.setChecked(True)
    dialog.select_profile(second.profile_id)
    assert dialog.current_profile.state is ProfileState.ARCHIVED
    assert not dialog.mapping_table.isEnabled()
    assert not dialog.restore_button.isHidden()
    dialog._restore_profile()
    assert dialog.current_profile.state is ProfileState.ACTIVE
    assert dialog.mapping_table.isEnabled()
    assert service.selected_revision("project-a", "variant-a") is None
    assert service.published_revision(second.profile_id, 1) is not None


def test_archive_dialog_renders_every_affected_variant(editor):
    service, dialog, _first, second = editor
    service.publish(second.profile_id, expected_draft_revision=0)
    for index in range(35):
        service.select("project-a", f"variant-{index:02d}", second.profile_id)
    observed = []

    def inspect_and_cancel():
        popup = _APP.activeModalWidget()
        assert isinstance(popup, QDialog)
        listing = popup.findChild(QListWidget)
        observed.extend(listing.item(index).text() for index in range(listing.count()))
        popup.reject()

    QTimer.singleShot(0, inspect_and_cancel)
    assert not confirm_archive(dialog, "B", service.list_profile_selections(second.profile_id), {"variant-00": "官中"})
    assert len(observed) == 35
    assert observed[0] == "官中（variant-00）"
    assert observed[-1] == "variant-34"


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.active_version_identity = ("project-a", "variant-a")
        self.project_name = "Old project"
        self.project_variants = ({"id": "variant-a", "name": "中文"},)


@pytest.mark.parametrize(
    "choice", [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel]
)
def test_context_switch_keeps_old_edit_service_and_new_manager_separate(monkeypatch, choice):
    services = {
        key: TerminologyProfileService(InMemoryTerminologyProfileRepository()) for key in ("project-a", "project-b")
    }
    old = services["project-a"].create_with_content("project-a", "A", _content("old"))
    adopted = services["project-a"].create_with_content("project-a", "B", _content("adopted"))
    services["project-a"].publish(adopted.profile_id, expected_draft_revision=0)
    services["project-a"].select("project-a", "variant-a", adopted.profile_id)
    new = services["project-b"].create_with_content("project-b", "New", _content("new"))
    ctx = _Context()
    bar = TerminologyProfileBar()
    controller = TerminologyProfileUiController(
        ctx,
        SimpleNamespace(profile_service_for=services.__getitem__),
        bar,
        SimpleNamespace(set_terminology_profile=lambda *_args, **_kwargs: None),
    )
    wait_profile(controller)
    controller.open_manager()
    previous = controller._manager
    assert previous.current_profile.profile_id == adopted.profile_id
    assert previous.mapping_table.model().setData(previous.mapping_table.model().index(0, 2), "local edit")
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: choice)
    ctx.active_version_identity = ("project-b", "variant-b")
    ctx.project_name = "New project"
    ctx.project_changed.emit()
    assert controller._manager is None
    assert previous.isVisible() == (choice == QMessageBox.StandardButton.Cancel)
    wait_profile(controller)
    controller.open_manager()
    assert controller._manager.current_profile.profile_id == new.profile_id
    if choice == QMessageBox.StandardButton.Cancel:
        assert "原工程：Old project" in previous.context_label.text()
        assert previous._save_draft()
        previous.close()
    old_saved = {item.profile_id: item for item in services["project-a"].list_profiles("project-a")}
    expected = "adopted" if choice == QMessageBox.StandardButton.Discard else "local edit"
    assert old_saved[adopted.profile_id].draft.mappings[0].translation == expected
    assert old_saved[old.profile_id].draft.mappings[0].translation == "old"
    assert services["project-b"].list_profiles("project-b")[0].draft.mappings[0].translation == "new"
    controller._manager.close()
    bar.close()
    _APP.processEvents()
