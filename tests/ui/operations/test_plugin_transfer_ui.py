from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QCheckBox, QFileDialog, QLineEdit
import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui.operations.test_operation_plan_coordinator_async import _DeferredWorker
from tests.ui.operations.test_production_facade import _Clock, _Ids
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.production import build_operation_plan_facade


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def transfer_ui(tmp_path, qapp):
    source = write_plugin(tmp_path / "project.esp", [(0x800, "NPC_A", "First")])
    target = write_plugin(tmp_path / "selected.esp", [(0x800, "NPC_A", "First")])
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("gui"), FormatId.PLUGIN_SSE)
    )
    assert parsed.entries
    context = SimpleNamespace(
        active_slot=SimpleNamespace(
            esp_path=str(source),
            format_id=FormatId.PLUGIN_SSE,
            source_snapshot=parsed.source_snapshot,
            collection=TranslationEntryCollection((replace(parsed.entries[0], translation="第一", stage=1),)),
        )
    )
    queued, workers = [], []
    tasks = TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, fn: queued.append(fn)),
    )
    facade = build_operation_plan_facade(SimpleNamespace(tasks=tasks), RequestContext("gui"))

    def worker_factory(fn):
        worker = _DeferredWorker(fn)
        workers.append(worker)
        return worker

    facade._coordinator._preflight_worker_factory = worker_factory
    dialog = facade.begin_write(context)
    case = SimpleNamespace(
        source=source, target=target, dialog=dialog, tasks=tasks, facade=facade, workers=workers, queued=queued
    )
    yield case
    if not sip.isdeleted(dialog):
        dialog.set_execution_running(False)
        dialog.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_plugin_picker_and_clear_choose_default_output_without_parsing(transfer_ui, monkeypatch):
    case = transfer_ui
    dialog = case.dialog

    def no_parse(*_args, **_kwargs):
        raise AssertionError("Picking a file must not parse it on the GUI thread")

    monkeypatch.setattr(SsePluginAdapter, "parse", no_parse)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(case.target), ""))
    dialog._overwrite.setChecked(True)
    dialog._browse_plugin()
    assert isinstance(dialog._overwrite, QCheckBox)
    assert not dialog._overwrite.isChecked()
    assert dict(dialog.edited_values())["plugin_path"] == str(case.target)
    assert dict(dialog.edited_values())["target_path"] == str(case.target)
    assert not any(editor.text() in {"true", "false"} for editor in dialog.findChildren(QLineEdit))
    dialog._select_plugin("")
    assert dict(dialog.edited_values())["plugin_path"] == ""
    assert dict(dialog.edited_values())["target_path"] == str(case.source)
    assert not case.workers


def test_output_picker_keeps_selected_plugin_and_requires_explicit_overwrite(transfer_ui, monkeypatch):
    case = transfer_ui
    dialog = case.dialog
    dialog._select_plugin(str(case.target))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(case.target), ""))
    dialog._browse_output()
    assert dict(dialog.edited_values())["plugin_path"] == str(case.target)
    assert dict(dialog.edited_values())["target_path"] == str(case.target)
    assert not dialog._overwrite.isChecked()
    dialog._preflight_button.click()
    case.workers[-1].complete()
    assert not dialog._confirm_button.isEnabled()
    assert "覆盖" in dialog._checks.text()
    dialog._edit_button.click()
    dialog._overwrite.setChecked(True)
    dialog._preflight_button.click()
    case.workers[-1].complete()
    assert dialog._confirm_button.isEnabled(), dialog._checks.text()


def test_transfer_preflight_is_deferred_and_submit_reuses_frozen_check(transfer_ui, monkeypatch):
    case = transfer_ui
    dialog = case.dialog
    parse = SsePluginAdapter.parse
    parsed_paths = []

    def track_parse(adapter, request):
        parsed_paths.append(request.source.uri)
        return parse(adapter, request)

    monkeypatch.setattr(SsePluginAdapter, "parse", track_parse)
    dialog._select_plugin(str(case.target))
    dialog._overwrite.setChecked(True)
    dialog._preflight_button.click()
    assert not parsed_paths
    assert dialog._preflight_running
    assert dialog._close_button.isEnabled()
    assert not dialog._fields_group.isEnabled()
    assert not dialog._confirm_button.isEnabled()
    case.workers[-1].complete()
    assert len(parsed_paths) == 1
    assert dialog._confirm_button.isEnabled(), dialog._checks.text()
    assert dialog._checks.text() == "✓ 预检通过"
    assert dialog._transfer_summary.isVisible()
    assert "精确匹配" in dialog._transfer_summary.text()
    assert "hydration" not in dialog._summary.text()
    assert "staging" not in dialog._summary.text()
    dialog._confirm_button.click()
    assert len(parsed_paths) == 1
    assert len(case.queued) == 1


@pytest.mark.parametrize("invalid", ["missing", "broken", "unmatched"])
def test_invalid_or_unmatched_plugin_reports_inline_and_keeps_confirm_disabled(transfer_ui, invalid):
    case = transfer_ui
    dialog = case.dialog
    if invalid == "missing":
        selected = case.target.with_name("missing.esp")
    elif invalid == "broken":
        selected = case.target
        selected.write_bytes(b"not an ESP")
    else:
        selected = write_plugin(case.target, [(0x800, "NPC_A", "Changed original")])
    dialog._select_plugin(str(selected))
    dialog._preflight_button.click()
    case.workers[-1].complete()
    assert not dialog._preflight_running
    assert dialog.isVisible()
    assert not dialog._confirm_button.isEnabled()
    assert dialog._edit_button.isEnabled()
    assert dialog._close_button.isEnabled()
    assert "套用" in dialog._checks.text()
    assert dialog._checks.property("tbStatusId") == "blocked"
    assert not case.queued


def test_changing_output_invalidates_old_confirmation(transfer_ui):
    case = transfer_ui
    dialog = case.dialog
    dialog._select_plugin(str(case.target))
    dialog._overwrite.setChecked(True)
    dialog._preflight_button.click()
    case.workers[-1].complete()
    token = dialog._preflight.confirmation_token
    assert token is not None
    dialog._field_edits["target_path"].setText(str(case.target.with_stem("another-output")))
    assert not dialog._confirm_button.isEnabled()
    assert dialog._preflight is None
    dialog.confirm_requested.emit(dialog._plan.session_id, token)
    assert not case.queued
    assert not case.tasks.list(OwnerRef("gui", "gui.operation-plan"))


def test_close_during_preflight_discards_late_results_and_session(transfer_ui, qapp):
    case = transfer_ui
    dialog = case.dialog
    dialog._select_plugin(str(case.target))
    dialog._preflight_button.click()
    assert dialog._preflight_running
    assert dialog.close()
    assert case.facade.active_plan_count == 0
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(dialog)
    case.workers[-1].complete()
    assert case.workers[-1].deleted
    assert not case.facade._coordinator._presenter._sessions
    assert not case.queued
