"""Exercise batch input preparation through real project persistence and write drafts."""

import json
from pathlib import Path
import sys
from threading import Event

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog

from tests.plugin_fixtures import write_plugin
from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as shared_plugin_fixture
from transbridge.application.projects import ProjectSourceRequest
from transbridge.ui.source_input_batch_dialog import SourceInputBatchDialog

shared_plugin_window = shared_plugin_fixture


def _two_sources(case, *, missing=(0, 1)):
    commands = case.services.gui_project_commands
    before = {row.entry_key for row in case.services.project_lifecycle.active.variant.snapshot().entries}
    other = write_plugin(case.root / "B.esp", [(0x900, "OtherNPC", "Another original")])
    added = commands.add_source(ProjectSourceRequest(str(other)), case.request)
    assert added.is_success, added.diagnostics
    for row in case.services.project_lifecycle.active.variant.snapshot().entries:
        if row.entry_key not in before:
            assert commands.update_entry(
                row.entry_key, case.request, translation="Project B translation", stage=1
            ).is_success
    assert commands.save(case.request).is_success
    active = case.services.project_lifecycle.active
    project = Path(case.services.projects.path_for(active.project_ref))
    document = json.loads(project.read_bytes())
    for index in missing:
        document["data"]["sources"][index]["source_artifact_id"] = "f" * 64
        document["data"]["sources"][index]["location"] = ""
    project.write_text(json.dumps(document), encoding="utf-8")
    case.window.project_coordinator.open_project_path(str(project))
    _until(lambda: case.window.project_open_worker is None and len(case.window.context.slots) == 2)
    assert sum(slot.source_snapshot is None for slot in case.window.context.slots.values()) == len(missing)
    return other, project


def _open_list(monkeypatch, action=None):
    opened = []
    original = SourceInputBatchDialog.open

    def show(dialog):
        opened.append(dialog)
        original(dialog)
        if action is not None:
            QTimer.singleShot(0, lambda: action(dialog))

    monkeypatch.setattr(SourceInputBatchDialog, "open", show)
    return opened


def _write_draft(facade):
    sessions = facade._coordinator._presenter._sessions
    assert len(sessions) == 1
    return next(iter(sessions.values())).draft.request


def test_one_multi_file_selection_repairs_all_sources_and_resumes_one_batch_draft(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    other, _project = _two_sources(case)
    before = {path: path.read_bytes() for path in (case.plugin, other)}
    selections = []

    def choose(*_args, **_kwargs):
        selections.append(True)
        return [str(case.plugin), str(other)], ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", choose)
    opened = _open_list(monkeypatch, lambda dialog: dialog.files_requested.emit())
    facade = case.window.operation_plan_facade
    assert facade.begin_write(case.window.context, batch=True) is None
    batch = case.window.source_input_coordinator._batch
    _until(lambda: selections and not batch._working and not batch._queue)
    assert all(need.ready for need in batch._needs()), (
        [(need.key, need.label, need.ready) for need in batch._needs()],
        batch.dialog.feedback.text(),
        batch.inputs._replacements,
    )
    assert selections == [True] and len(opened) == 1
    assert facade.active_plan_count == 0  # Supplementation never starts writing without scope confirmation.
    assert batch.dialog.model.rowCount() == 2
    assert not case.window.app_runtime.use_cases.resolve("project_source_supplements")._pending
    batch.dialog.continued.emit(tuple(need.key for need in batch._needs()))
    _until(lambda: facade.active_plan_count == 1)
    plan = next(iter(facade._coordinator._owned_windows.values()))
    try:
        draft = _write_draft(facade)
        assert len(draft.drafts) == 2 and draft.failures == ()
        assert {entry.translation for item in draft.drafts for entry in item.entries} == {
            "Project A translation",
            "Project B translation",
        }
        assert batch.dialog is None
        assert not case.window.context.authoritative_projection_diverged()
        assert all(path.read_bytes() == payload for path, payload in before.items())
    finally:
        plan.close()


def test_cancel_list_after_one_repair_keeps_saved_input_and_other_translations(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    _other, project = _two_sources(case)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(case.plugin), ""))
    _open_list(
        monkeypatch,
        lambda dialog: dialog.source_requested.emit(
            next(need.key for need in dialog.model.needs if need.label == "A.esp")
        ),
    )
    facade = case.window.operation_plan_facade
    assert facade.begin_write(case.window.context, batch=True) is None
    batch = case.window.source_input_coordinator._batch
    _until(lambda: sum(need.ready for need in batch._needs()) == 1 and not batch._working)
    batch.dialog.reject()
    assert batch.dialog is None and facade.active_plan_count == 0
    assert not case.window.app_runtime.use_cases.resolve("project_source_supplements")._pending
    case.window.project_coordinator.open_project_path(str(project))
    _until(lambda: case.window.project_open_worker is None)
    assert sum(slot.source_snapshot is not None for slot in case.window.context.slots.values()) == 1
    assert {entry.translation for slot in case.window.context.slots.values() for entry in slot.collection} == {
        "Project A translation",
        "Project B translation",
    }
    assert case.window.workbench.isEnabled() and not case.window.context.authoritative_projection_diverged()


def test_skip_missing_source_rebuilds_batch_with_only_one_ready_source_and_preflights(
    shared_plugin_window, monkeypatch
):
    case = shared_plugin_window
    _other, _project = _two_sources(case, missing=(1,))
    output = case.root / "output"
    output.mkdir()
    opened = _open_list(monkeypatch)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda _kind, value, _tb: errors.append(value))
    facade = case.window.operation_plan_facade
    assert facade.begin_write(case.window.context, batch=True, output_directory=str(output)) is None
    batch = case.window.source_input_coordinator._batch
    assert len(opened) == 1 and batch.dialog.model.rowCount() == 1
    batch.dialog.skip_checkbox.click()
    batch.dialog.review_button.click()
    assert facade.active_plan_count == 0
    assert "跳过 1 个输入" in batch.dialog.scope_summary.text()
    batch.dialog.confirm_button.click()
    _until(lambda: facade.active_plan_count == 1 or errors)
    assert not errors, errors
    plan = next(iter(facade._coordinator._owned_windows.values()))
    try:
        draft = _write_draft(facade)
        assert len(draft.drafts) == 1 and draft.failures == ()
        assert len(draft.drafts[0].entries) == 1
        assert draft.drafts[0].entries[0].translation == "Project A translation"
        assert draft.drafts[0].target_path == str(output / "A.esp")
        assert sum(slot.source_snapshot is None for slot in case.window.context.slots.values()) == 1
        plan._request_preflight()
        _until(lambda: not facade._coordinator._preflight_workers and plan._preflight is not None)
        assert plan._preflight.ready, plan._checks.text()
        assert list(output.iterdir()) == []
    finally:
        plan.close()


def test_cancel_single_file_picker_keeps_missing_input_list_open(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    _two_sources(case)
    calls = []

    def choose(*_args, **_kwargs):
        calls.append(True)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    _open_list(monkeypatch, lambda dialog: dialog.source_requested.emit(dialog.model.needs[0].key))
    facade = case.window.operation_plan_facade
    facade.begin_write(case.window.context, batch=True)
    batch = case.window.source_input_coordinator._batch
    _until(lambda: bool(calls))
    assert batch.dialog is not None and batch.dialog.isVisible()
    assert not batch._working and facade.active_plan_count == 0
    assert not any(need.ready for need in batch._needs())
    batch.dialog.reject()


def test_cancel_during_first_supplement_finishes_current_item_and_stops_queue(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    other, _project = _two_sources(case)
    service = case.window.app_runtime.use_cases.resolve("project_source_supplements")
    entered, release = Event(), Event()
    prepare = service.prepare
    prepared = []

    def blocked(*args, **kwargs):
        prepared.append(args)
        entered.set()
        assert release.wait(5)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(service, "prepare", blocked)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: ([str(case.plugin), str(other)], ""))
    _open_list(monkeypatch, lambda dialog: dialog.files_requested.emit())
    facade = case.window.operation_plan_facade
    facade.begin_write(case.window.context, batch=True)
    batch = case.window.source_input_coordinator._batch
    try:
        _until(entered.is_set)
        assert batch._working and len(batch._queue) == 1
        batch.dialog.reject()
        assert batch.dialog is not None  # Keep the current publication alive until reopening completes.
    finally:
        release.set()
    _until(lambda: batch.dialog is None and not batch._working)
    assert len(prepared) == 1 and facade.active_plan_count == 0
    assert sum(slot.source_snapshot is not None for slot in case.window.context.slots.values()) == 1
    assert sum(slot.source_snapshot is None for slot in case.window.context.slots.values()) == 1
    assert not service._pending and not case.window.source_input_coordinator._busy
    assert {entry.translation for slot in case.window.context.slots.values() for entry in slot.collection} == {
        "Project A translation",
        "Project B translation",
    }
