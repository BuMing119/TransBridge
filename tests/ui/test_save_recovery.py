from __future__ import annotations

from dataclasses import replace
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox
import pytest

from tests.ui import test_save_completion_state, test_worker_shutdown_registry
from transbridge.application.projections.models import ProjectionSnapshot
from transbridge.application.projections.store import ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.main_window import MainWindow
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.shell import save_recovery, window_lifecycle
from transbridge.ui.shell.save_recovery import SaveRecovery, write_recovery_bundle
from transbridge.ui.shell.window_lifecycle import AutoSaveManager
from transbridge.ui.worker_registry import get_api_worker_registry
from transbridge.ui.workers import ApiWorker

host = test_worker_shutdown_registry.host
save_host = test_save_completion_state.save_host
_wait = test_worker_shutdown_registry._wait


class RecoveryContext(QObject):
    dirty_changed = pyqtSignal()
    collection_changed = pyqtSignal(object)
    collection_list_changed = pyqtSignal()
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)
    label_data_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.dirty = True
        self.workspace = None
        self.close_projection = Mock()
        self.active_project_id = "project"
        self.active_variant_id = "default"
        self.project_revision = self.variant_revision = 3
        self.active_key = "source-b"
        self.label_library = {"review": {"name": "待校对", "color": "#123456"}}
        self._entry_labels = {"same": {"review"}}
        self._entry_labels_exact = {("source-a", "same"): {"review"}}
        self._project_projection = ProjectionStore(
            ProjectionSnapshot("project", 3, 2, {"project_id": "project", "label_library": self.label_library})
        )
        self.slots = {}
        for key, translation in (("source-a", "旧工程未保存译文"), ("source-b", "当前工程未保存译文")):
            entry = TranslationEntry(
                "same",
                "same",
                "Original",
                translation,
                3,
                "INFO NAM1",
                metadata=(("review", {"note": "保留元数据"}),),
            )
            collection = TranslationEntryCollection()
            collection.add(entry)
            self.slots[key] = CollectionSlot(key, collection, esp_path=f"{key}.esp")

    def edit(self):
        collection = self.slots["source-a"].collection
        entry = next(iter(collection))
        collection.add(replace(entry, translation="备份之后的新译文"), overwrite=True)
        self.dirty_changed.emit()


def _prepare_recovery(host, monkeypatch, destination):
    context = RecoveryContext()
    host.context = context
    host.lifecycle._recovery = SaveRecovery(context)
    host.save_ok = False
    host.choose_recovery.return_value = destination
    monkeypatch.setattr(window_lifecycle, "choose_recovery_destination", host.choose_recovery)
    return context


def test_recovery_exports_every_source_and_roundtrips_full_entries_and_metadata(tmp_path):
    context = RecoveryContext()
    recovery = SaveRecovery(context)
    capture = recovery.capture()
    destination = tmp_path / "recovery.zip"
    write_recovery_bundle(destination, capture)
    assert recovery.accept(capture) and recovery.verified
    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["sources"]) == 2
        assert manifest["labels"]["exact_entries"] == [{"identity": ["source-a", "same"], "labels": ["review"]}]
        assert manifest["authority"]["revision"] == 3
        assert manifest["authority"]["values"]["label_library"] == context.label_library
        for index, source in enumerate(manifest["sources"]):
            path = tmp_path / f"restored-{index}.json"
            path.write_bytes(archive.read(source["file"]))
            restored = TranslationEntryCollection.from_json_file(path)
            assert restored.to_dict() == context.slots[source["key"]].collection.to_dict()
        assert "导入已有译文" in archive.read("恢复说明.txt").decode("utf-8")
    assert not list(tmp_path.glob(".transbridge-recovery-*"))
    context.edit()
    assert not recovery.verified


def test_recovery_prompt_defaults_to_return_and_does_not_export_without_consent(host, monkeypatch):
    observed = []

    def reject(dialog):
        observed.append((dialog.defaultButton().text(), dialog.escapeButton().text()))
        dialog.reject()

    monkeypatch.setattr(QMessageBox, "exec", reject)
    destination = Mock()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", destination)
    assert save_recovery.choose_recovery_destination(host) is None
    assert observed == [("返回工作台", "返回工作台")]
    destination.assert_not_called()


def test_failed_recovery_replace_preserves_existing_file_and_removes_temporary_copy(tmp_path, monkeypatch):
    destination = tmp_path / "recovery.zip"
    destination.write_bytes(b"previous backup")
    replace_file = Mock(side_effect=OSError("destination is in use"))
    monkeypatch.setattr(save_recovery.os, "replace", replace_file)
    with pytest.raises(OSError, match="destination is in use"):
        write_recovery_bundle(destination, SaveRecovery(RecoveryContext()).capture())
    assert destination.read_bytes() == b"previous backup"
    assert not list(tmp_path.glob(".transbridge-recovery-*"))


def test_same_size_collection_update_invalidates_receipt_without_context_signal():
    context = RecoveryContext()
    recovery = SaveRecovery(context)
    assert recovery.accept(recovery.capture())
    collection = context.slots["source-a"].collection
    entry = next(iter(collection))
    collection.add(replace(entry, translation="后台更新"), overwrite=True)
    assert len(collection) == 1
    assert not recovery.verified


def test_capture_keeps_original_source_when_mutable_slot_is_replaced(tmp_path):
    context = RecoveryContext()
    recovery = SaveRecovery(context)
    capture = recovery.capture()
    slot = context.slots["source-a"]
    original = slot.collection.to_dict()
    slot.collection = TranslationEntryCollection()
    slot.label = "replacement"
    slot.esp_path = "replacement.esp"
    destination = tmp_path / "recovery.zip"
    write_recovery_bundle(destination, capture)
    assert not recovery.accept(capture)
    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        source = manifest["sources"][0]
        assert source["label"] == "source-a" and source["paths"]["esp_path"] == "source-a.esp"
        assert json.loads(archive.read(source["file"])) == original


def test_explicit_recovery_exit_preserves_dirty_data_in_verified_backup(host, monkeypatch, tmp_path):
    destination = tmp_path / "recovery.zip"
    context = _prepare_recovery(host, monkeypatch, destination)
    host.close()
    _wait(lambda: host.close_ready)
    assert context.dirty
    context.close_projection.assert_called_once()
    assert destination.exists()
    assert host.order == ["save", "closed"]


def test_recovery_error_keeps_dirty_data_and_window_open(host, monkeypatch, tmp_path):
    destination = tmp_path / "recovery.zip"
    context = _prepare_recovery(host, monkeypatch, destination)

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(window_lifecycle, "write_recovery_bundle", fail)
    host.close()
    _wait(lambda: not host.close_pending)
    assert context.dirty and host.isVisible() and host.isEnabled()
    assert not host.close_ready and not destination.exists()
    context.close_projection.assert_not_called()
    assert "disk full" in QMessageBox.warning.call_args.args[2]


def test_edits_during_recovery_export_are_not_authorized_for_exit(host, monkeypatch, tmp_path):
    context = _prepare_recovery(host, monkeypatch, tmp_path / "recovery.zip")
    entered, release = threading.Event(), threading.Event()

    def delayed(path, capture):
        entered.set()
        assert release.wait(5)
        return write_recovery_bundle(path, capture)

    monkeypatch.setattr(window_lifecycle, "write_recovery_bundle", delayed)
    try:
        host.close()
        _wait(entered.is_set)
        context.edit()
        release.set()
        _wait(lambda: not host.close_pending)
        assert context.dirty and not host.close_ready
        context.close_projection.assert_not_called()
        assert "内容有变化" in QMessageBox.warning.call_args.args[2]
    finally:
        release.set()
        _wait(lambda: not get_api_worker_registry().busy)


@pytest.mark.parametrize("new_edit", [False, True])
def test_verified_backup_waits_for_new_worker_and_rejects_later_edits(host, monkeypatch, tmp_path, new_edit):
    context = _prepare_recovery(host, monkeypatch, tmp_path / "recovery.zip")
    release = threading.Event()
    follow_up = ApiWorker(lambda: release.wait(5), route_http_errors=False)
    if new_edit:
        follow_up.result.connect(lambda _: context.edit())
    original_accept = host.lifecycle._recovery.accept

    def accept(capture):
        accepted = original_accept(capture)
        follow_up.start()
        return accepted

    monkeypatch.setattr(host.lifecycle._recovery, "accept", accept)
    try:
        host.close()
        _wait(follow_up.isRunning)
        assert not host.close_ready
        context.close_projection.assert_not_called()
        release.set()
        _wait(lambda: not host.close_pending or host.close_ready)
        assert host.close_ready is not new_edit
        assert context.dirty
        if new_edit:
            context.close_projection.assert_not_called()
            assert "内容又有变化" in QMessageBox.warning.call_args.args[2]
        else:
            context.close_projection.assert_called_once()
    finally:
        release.set()
        follow_up.wait(2000)


def test_failed_autosave_stays_paused_until_manual_success(save_host):
    host = save_host
    host.context = host._ctx
    host._ctx.variant_store = None
    host.save_current_project_async = lambda **kwargs: MainWindow.save_current_project_async(host, **kwargs)
    manager = host._auto_saver = AutoSaveManager(host, debounce_ms=5)
    attempts = []

    def fail():
        attempts.append("failed")
        raise OSError("disk full")

    host._save_current_project = fail
    try:
        manager._auto_save()
        _wait(lambda: host._save_worker is None)
        assert host._ctx.dirty and attempts == ["failed"] and len(host.errors) == 1
        for _ in range(5):
            manager.trigger_debounce()
            manager._auto_save()
        assert not manager._debounce_timer.isActive() and attempts == ["failed"]

        def succeed():
            host._ctx.dirty = False

        host._save_current_project = succeed
        host.save_current_project_async()
        _wait(lambda: host._save_worker is None)
        host._ctx.dirty = True
        manager.trigger_debounce()
        assert manager._debounce_timer.isActive()
    finally:
        manager.stop()


@pytest.mark.parametrize("field", ["active_variant_id", "active_project_id"])
def test_project_or_variant_switch_restores_autosave_after_failure(field):
    context = RecoveryContext()
    host = SimpleNamespace(context=context)
    manager = AutoSaveManager(host)
    try:
        manager.record_save_result(False)
        context.project_changed.emit()
        assert not manager._debounce_timer.isActive()
        setattr(context, field, "new")
        context.project_changed.emit()
        assert manager._debounce_timer.isActive()
    finally:
        manager.stop()


def test_successful_save_with_new_edits_keeps_autosave_enabled(save_host):
    host = save_host
    host.context = host._ctx
    host._ctx.variant_store = None
    host.save_current_project_async = lambda **kwargs: MainWindow.save_current_project_async(host, **kwargs)
    host._save_current_project = lambda: None
    manager = host._auto_saver = AutoSaveManager(host)
    try:
        manager._auto_save()
        _wait(lambda: host._save_worker is None)
        assert host._ctx.dirty and manager._debounce_timer.isActive()
        assert not host.errors
    finally:
        manager.stop()


@pytest.mark.parametrize("failure", ["construct", "start"])
def test_foreground_start_failure_cleans_up_and_delivers_error_callbacks(save_host, monkeypatch, failure):
    host = save_host
    host._workbench.show_step2_progress = Mock()
    host._workbench.hide_step2_progress = Mock()
    host._workbench.setEnabled = Mock()
    before_start, on_error, on_finished = Mock(), Mock(), Mock()
    failing = Mock(side_effect=RuntimeError("thread unavailable"))
    if failure == "construct":
        monkeypatch.setattr("transbridge.ui.main_window.ApiWorker", failing)
    else:
        monkeypatch.setattr(ApiWorker, "start", failing)
    assert MainWindow.start_foreground_task(
        host,
        lambda: None,
        message="test",
        before_start=before_start,
        on_error=on_error,
        on_finished=on_finished,
    )
    before_start.assert_called_once()
    on_error.assert_called_once()
    assert "thread unavailable" in on_error.call_args.args[0]
    on_finished.assert_called_once()
    assert host._foreground_worker is None and not host._workers
    host._workbench.hide_step2_progress.assert_called_once()
    host._workbench.setEnabled.assert_called_with(True)


def test_foreground_before_start_is_guarded_and_failure_does_not_change_ui(save_host):
    host = save_host
    host._workbench.show_step2_progress = Mock()
    before_start = Mock(side_effect=ValueError("cannot prepare"))
    host._save_worker = object()
    assert not MainWindow.start_foreground_task(host, lambda: None, message="test", before_start=before_start)
    before_start.assert_not_called()
    host._save_worker = None
    with pytest.raises(ValueError, match="cannot prepare"):
        MainWindow.start_foreground_task(host, lambda: None, message="test", before_start=before_start)
    host._workbench.show_step2_progress.assert_not_called()
    assert host._foreground_worker is None and not host._workers


@pytest.mark.parametrize("failure", ["construct", "start"])
def test_save_worker_start_failure_preserves_dirty_and_completes_callback(save_host, monkeypatch, failure):
    host = save_host
    host._save_current_project = lambda: None
    callback = Mock()
    failing = Mock(side_effect=RuntimeError("thread unavailable"))
    if failure == "construct":
        monkeypatch.setattr("transbridge.ui.main_window.ApiWorker", failing)
    else:
        monkeypatch.setattr(ApiWorker, "start", failing)
    assert MainWindow.save_current_project_async(host, on_finished=callback)
    callback.assert_called_once_with(False)
    assert host._ctx.dirty and host._save_worker is None and not host._workers
    assert "thread unavailable" in host.errors[0]


def test_recovery_worker_constructor_failure_keeps_window_and_dirty_state(host, monkeypatch, tmp_path):
    context = _prepare_recovery(host, monkeypatch, tmp_path / "recovery.zip")
    monkeypatch.setattr(window_lifecycle, "ApiWorker", Mock(side_effect=RuntimeError("thread unavailable")))
    host.close()
    _wait(lambda: not host.close_pending)
    assert host.isEnabled() and not host.close_ready and context.dirty
    context.close_projection.assert_not_called()
    assert "thread unavailable" in QMessageBox.warning.call_args.args[2]
