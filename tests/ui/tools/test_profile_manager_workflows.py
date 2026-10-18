from dataclasses import replace
from threading import Event, get_ident

from PyQt6.QtCore import QItemSelectionModel, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QListWidget, QMessageBox, QTableView
import pytest

from tests.ui.workbench.terminology_async_support import wait_until
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.ui.tools.terminology_profiles import (
    TerminologyProfileManagerDialog,
    manager_dialog as manager_module,
    manager_workflows as workflows,
)
from transbridge.ui.tools.terminology_profiles.publication_dialog import confirm_publication

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def manager(monkeypatch):
    repository = InMemoryTerminologyProfileRepository()
    service = TerminologyProfileService(repository)
    content = TerminologyProfileContent(
        tuple(
            ProfileTermMapping(name, target, base)
            for name, target, base in (
                ("Alpha", "阿尔法", "默认甲"),
                ("Beta", "贝塔", "默认乙"),
                ("Gamma", "伽马", "默认丙"),
            )
        )
    )
    profile = service.create_with_content("project", "方案 A", content)
    service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project", "current", profile.profile_id)
    dialog = TerminologyProfileManagerDialog(
        service,
        "project",
        selected_profile_id=profile.profile_id,
        variant_id="current",
        variant_names={"current": "中文"},
    )
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    yield service, repository, dialog, profile
    wait_until(lambda: not dialog._workflows.active)
    dialog.close()
    _APP.processEvents()


def test_search_all_three_names_and_remove_multiple_filtered_rows_only_after_confirmation(manager, monkeypatch):
    _service, _repository, dialog, _profile = manager
    model = dialog.mapping_table.model()
    for query, original in (("alp", "Alpha"), ("默认乙", "Beta"), ("伽马", "Gamma")):
        dialog.mapping_search.setText(query)
        assert model.rowCount() == 1
        assert model.index(0, 0).data() == original
    dialog.mapping_search.setText("默认")
    selection = dialog.mapping_table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.select(model.index(0, 0), flags)
    selection.select(model.index(2, 0), flags)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel)
    dialog.remove_mapping_button.click()
    assert model.rowCount() == 3
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    dialog.remove_mapping_button.click()
    assert model.rowCount() == 1
    assert model.index(0, 0).data() == "Beta"
    assert [item.original for item in dialog.mapping_table.read_mappings()] == ["Beta"]


def test_edit_filtered_row_and_changed_filter_keep_correct_source_identity(manager):
    service, _repository, dialog, profile = manager
    dialog.open_intent(original="Beta")
    model = dialog.mapping_table.model()
    assert model.rowCount() == 1
    assert dialog.mapping_table.currentIndex().column() == 2
    assert dialog.mapping_table.currentIndex().data() == "贝塔"
    assert model.setData(model.index(0, 2), "新贝塔")
    dialog.mapping_search.clear()
    dialog.changed_only.setChecked(True)
    assert model.rowCount() == 1
    assert model.index(0, 0).data() == "Beta"
    assert dialog._save_draft()
    assert not dialog.mapping_table.is_dirty
    assert model.rowCount() == 1  # Saved draft still differs from the published content.
    assert service.published_revision(profile.profile_id, 1).content.mappings[1].translation == "贝塔"
    dialog.add_mapping_button.click()
    assert not dialog.changed_only.isChecked()
    assert model.rowCount() == 4
    assert dialog.mapping_table.currentIndex().row() == 3


def test_cancel_publication_keeps_unsaved_inputs_without_writing(manager, monkeypatch):
    service, repository, dialog, profile = manager
    model = dialog.mapping_table.model()
    model.setData(model.index(0, 2), "新阿尔法")
    previews = []
    monkeypatch.setattr(
        workflows, "confirm_publication", lambda _owner, preview, _names: previews.append(preview) or False
    )
    dialog.publish_button.click()
    wait_until(lambda: not dialog._workflows.active)
    assert len(previews) == 1 and previews[0].changes[0].after.translation == "新阿尔法"
    assert model.index(0, 2).data() == "新阿尔法"
    assert dialog.mapping_table.is_dirty
    assert repository.get_profile(profile.profile_id).draft == profile.draft
    assert service.selected_revision("project", "current").revision == 1


def test_publication_with_changed_users_does_not_save_or_publish(manager, monkeypatch):
    service, repository, dialog, profile = manager
    dialog.mapping_table.model().setData(dialog.mapping_table.model().index(0, 2), "新阿尔法")

    def confirm(_owner, _preview, _names):
        service.select("project", "unexpected-user", profile.profile_id)
        return True

    monkeypatch.setattr(workflows, "confirm_publication", confirm)
    dialog.publish_button.click()
    wait_until(lambda: not dialog._workflows.active)
    assert "使用此方案的翻译版本" in dialog.status_label.text()
    assert repository.get_profile(profile.profile_id).draft == profile.draft
    assert service.selected_revision("project", "current").revision == 1
    assert dialog.mapping_table.is_dirty


def test_no_change_does_not_prompt_or_create_another_revision(manager, monkeypatch):
    service, _repository, dialog, profile = manager
    confirmations = []
    monkeypatch.setattr(workflows, "confirm_publication", lambda *_args: confirmations.append(True) or True)
    dialog.publish_button.click()
    wait_until(lambda: not dialog._workflows.active)
    assert confirmations == []
    assert "没有需要发布" in dialog.status_label.text()
    assert service.published_revision(profile.profile_id, 2) is None


def test_publish_and_use_adopts_created_scheme_after_confirming_current_version(manager, monkeypatch):
    service, _repository, dialog, _profile = manager
    second = service.create_with_content(
        "project", "方案 B", TerminologyProfileContent((ProfileTermMapping("A", "乙"),))
    )
    dialog.select_profile(second.profile_id)
    previews = []
    monkeypatch.setattr(workflows, "confirm_publication", lambda _owner, value, _names: previews.append(value) or True)
    dialog.publish_use_button.click()
    wait_until(lambda: not dialog._workflows.active)
    assert previews[0].target_variant_id == "current"
    assert service.selected_revision("project", "current").profile_id == second.profile_id
    assert "已用于当前翻译版本" in dialog.status_label.text()


def test_name_only_adoption_reviews_rename_and_uses_new_name_in_success(manager, monkeypatch):
    service, _repository, dialog, profile = manager
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("新方案名称", True))
    dialog._rename_profile()
    assert "使用中的版本将采用新名称" in dialog.status_label.text()
    previews = []
    monkeypatch.setattr(workflows, "confirm_publication", lambda _owner, value, _names: previews.append(value) or True)
    dialog.publish_use_button.click()
    wait_until(lambda: not dialog._workflows.active)
    assert len(previews) == 1 and previews[0].previous_name == "方案 A"
    assert previews[0].changes == () and previews[0].has_changes
    assert service.selected_revision("project", "current").name == "新方案名称"
    assert service.published_revision(profile.profile_id, 1).name == "方案 A"
    assert "新方案名称" in dialog.status_label.text()
    assert "方案 A" not in dialog.status_label.text()


def test_slow_publication_preview_runs_off_qt_thread_and_close_waits(manager, monkeypatch):
    service, _repository, dialog, _profile = manager
    started, release = Event(), Event()
    threads = []
    original = service.preview_publication

    def slow(*args, **kwargs):
        threads.append(get_ident())
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "preview_publication", slow)
    dialog.show()
    dialog.publish_button.click()
    try:
        wait_until(started.is_set)
        assert threads[0] != get_ident()
        assert not dialog.close()
        assert dialog.isVisible()
    finally:
        release.set()
    wait_until(lambda: not dialog._workflows.active)


def test_create_from_project_terminology_reads_in_background_and_does_not_change_selection(monkeypatch):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    threads = []
    snapshot = EffectiveTerminologySnapshot(
        "project",
        "current",
        EffectiveSnapshotStatus.READY,
        version_id="base",
        content_digest="a" * 64,
        decisions=(
            TermDecision("a", "project", "current", "A", "a", "甲", TermScope.project(), DecisionStatus.ADOPTED),
        ),
    )

    def read():
        threads.append(get_ident())
        return snapshot

    dialog = TerminologyProfileManagerDialog(service, "project", variant_id="current", base_snapshot_reader=read)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("自定义方案", True))
    dialog.open_intent(create_from_base=True)
    wait_until(lambda: not dialog._workflows.active)
    assert threads and threads[0] != get_ident()
    assert dialog.current_profile.name == "自定义方案"
    assert dialog.current_profile.draft.mappings[0] == ProfileTermMapping("A", "甲", "甲")
    assert service.selected_revision("project", "current") is None
    assert dialog.current_profile.latest_published_revision is None
    dialog.close()


def test_publication_dialog_renders_exact_changes_and_all_affected_versions(manager):
    service, _repository, dialog, profile = manager
    for index in range(30):
        service.select("project", f"variant-{index:02d}", profile.profile_id)
    changed = replace(profile.draft, mappings=profile.draft.mappings[1:])
    preview = service.preview_publication(profile.profile_id, changed, expected_revision=0, target_variant_id="new")
    observed = []

    def inspect():
        popup = _APP.activeModalWidget()
        assert isinstance(popup, QDialog)
        table = popup.findChild(QTableView)
        assert table.model().rowCount() == 1
        assert table.model().index(0, 0).data() == "移除"
        assert table.model().index(0, 1).data() == "Alpha"
        listing = popup.findChild(QListWidget)
        observed.extend(listing.item(index).text() for index in range(listing.count()))
        popup.reject()

    QTimer.singleShot(0, inspect)
    assert not confirm_publication(dialog, preview, {"current": "中文"})
    assert len(observed) == 32
    assert any("中文" in text for text in observed)
    assert any("new（本次采用此方案）" in text for text in observed)
