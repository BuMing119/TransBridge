from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit
import pytest

from tests.ui.operations.test_production_facade import _ConfirmingDialog
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.tasks import JobSpec, OwnerRef, TaskCenterAction, TaskCenterActionError
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.bootstrap.composition import build_runtime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.operations.production import build_operation_plan_facade
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog
from transbridge.ui.workbench.widget import WorkbenchWidget
from transbridge.ui.worker_registry import get_api_worker_registry


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def desktop(tmp_path, monkeypatch, qapp):
    runtime = build_runtime({"persistence_v2_root": tmp_path / "v2", "ui_config_path": tmp_path / "config.json"})
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    identity = [("project-one", "variant-one")]
    monkeypatch.setattr(context_module.AppContext, "active_version_identity", property(lambda _: identity[0]))
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name, source.stat().st_size),
            RequestContext("gui-owner"),
            FormatId.XML_EET,
        )
    )
    context = context_module.AppContext()
    context.add_slot(
        str(source),
        context_module.CollectionSlot(
            label="Source",
            collection=TranslationEntryCollection(parsed.entries),
            esp_path=str(source),
            format_id=FormatId.XML_EET,
            source_snapshot=parsed.source_snapshot,
        ),
    )
    host = QMainWindow()
    host.context = context
    host.app_runtime = runtime
    host.runtime_context = RequestContext("gui-owner", metadata=(("entrypoint", "gui"),))
    host.close_pending = False
    host.workbench = WorkbenchWidget(context, host, project_directory_provider=lambda: tmp_path / "project")
    host.setCentralWidget(host.workbench)
    for name in (
        "variant_coordinator",
        "tool_windows",
        "project_transfer_coordinator",
        "parse_coordinator",
        "project_coordinator",
        "project_management_coordinator",
        "source_update_coordinator",
        "start_center_controller",
        "operation_coordinator",
        "show_message",
        "show_about",
    ):
        setattr(host, name, Mock())
    facade = build_operation_plan_facade(runtime, host.runtime_context, dialog_factory=_ConfirmingDialog)
    results = {}
    monkeypatch.setattr(facade, "_execution_result_reader", lambda ref, _actor: results.get(ref.run_id))
    host.operation_plan_facade = facade
    host.operation_coordinator.write = lambda: facade.begin_write(context, target_path=str(tmp_path / "retry.xml"))
    composition = ShellIntentComposition(host)
    composition.start()
    actor = OwnerRef("gui-owner", "gui", permissions=frozenset({"tasks:manage"}))
    yield SimpleNamespace(
        runtime=runtime,
        host=host,
        composition=composition,
        actor=actor,
        identity=identity,
        results=results,
        facade=facade,
        directory=tmp_path / "project",
        output=tmp_path / "retry.xml",
    )
    composition.close()
    host.workbench.ai_tasks.shutdown()
    _until(lambda: not host.workbench.ai_tasks.busy and not get_api_worker_registry().busy)
    host.workbench.ai_tasks.dispose()
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    runtime.close()


def _record(desktop, *, kind="operation.write", failed=True, result=None):
    owner = OwnerRef("gui-owner", "gui.operation-plan", project_id="project-one", variant_id="variant-one")
    ref = desktop.runtime.tasks.submit(JobSpec(kind, "source-current", "fingerprint", "Reviewed operation"), owner).ref
    desktop.runtime.tasks.start(ref, owner)
    if result is not None:
        desktop.results[ref.run_id] = result
    (desktop.runtime.tasks.fail if failed else desktop.runtime.tasks.complete)(ref, owner)
    actions = desktop.runtime.use_cases.resolve("task_center_actions")
    item = next(
        value for value in actions.list_history(desktop.actor, retry_context=None) if value.run_id == ref.run_id
    )
    return ref, item


def _panel(desktop, run_id, *, history=True):
    assert desktop.composition.dispatch(IntentId.TASK_OPEN_ACTIVITY).accepted
    panel = desktop.composition._task_dock.widget()
    task_list = panel._history if history else panel._current
    panel._tabs.setCurrentWidget(task_list)
    for index in range(task_list.count()):
        value = task_list.item(index).data(Qt.ItemDataRole.UserRole)
        if (value.run_id if history else value) == run_id:
            task_list.setCurrentRow(index)
            break
    else:
        pytest.fail("terminal task was not present in the production task center")
    return panel


def test_retry_guidance_opens_task_center_without_submitting_a_task(desktop):
    before = desktop.runtime.tasks.list(desktop.actor)
    assert desktop.composition.dispatch(IntentId.TASK_RETRY).accepted
    assert desktop.composition._task_dock.isVisible()
    assert desktop.runtime.tasks.list(desktop.actor) == before


@pytest.mark.parametrize("kind,available", [("operation.fomod", True), ("operation.write", False)])
def test_archive_preparation_does_not_require_an_active_translation_collection(desktop, kind, available):
    desktop.host.context.collection = None
    _ref, item = _record(desktop, kind=kind)
    assert item.available_actions.retry is available


@pytest.mark.parametrize("history", [False, True])
def test_terminal_result_routes_from_task_center_through_real_action_and_receipt(desktop, history):
    result = OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic("WRITE_DENIED", "Output directory is read-only"),),
        counts=OperationCounts(failed=1),
    )
    ref, _item = _record(desktop, result=result)
    panel = _panel(desktop, ref.run_id, history=history)
    assert panel._open_result.isEnabled() and panel._open_log.isEnabled()
    panel._open_result.click()
    dialogs = desktop.composition._task_navigation._dialogs
    assert len(dialogs) == 1
    text = dialogs[0].findChild(QPlainTextEdit).toPlainText()
    assert ref.run_id in text and "Output directory is read-only" in text and "WRITE_DENIED" in text
    assert len(desktop.runtime.tasks.list(desktop.actor)) == 1


def test_prepare_retry_opens_new_plan_without_submitting_or_writing(desktop):
    ref, item = _record(desktop)
    assert item.available_actions.retry and item.retry_requires_preparation
    panel = _panel(desktop, ref.run_id)
    assert panel._retry.text() == "重新准备…"
    panel._retry.click()
    assert desktop.facade.active_plan_count == 1
    assert len(desktop.runtime.tasks.list(desktop.actor)) == 1
    assert not desktop.output.exists()


@pytest.mark.parametrize("identity", [("other-project", "variant-one"), ("project-one", "other-variant")])
def test_prepared_intent_rechecks_current_project_and_variant(desktop, identity):
    _ref, item = _record(desktop)
    actions = desktop.runtime.use_cases.resolve("task_center_actions")
    intent = actions.execute(item, TaskCenterAction.RETRY, desktop.actor, retry_context=None).navigation
    desktop.identity[0] = identity
    with pytest.raises(ValueError, match="工程、版本"):
        desktop.composition._task_navigation.dispatch(intent)
    assert desktop.facade.active_plan_count == 0
    assert len(desktop.runtime.tasks.list(desktop.actor)) == 1


def test_recovery_permission_is_rechecked_when_prepared_intent_is_dispatched(desktop):
    _ref, item = _record(desktop)
    actions = desktop.runtime.use_cases.resolve("task_center_actions")
    revoked = [False]

    def preflight(*_args):
        if revoked[0]:
            raise RequestError("REQUEST_RECOVERY_REQUIRED", "Original request was revoked")

    actions.set_request_preflight(preflight)
    intent = actions.execute(item, TaskCenterAction.RETRY, desktop.actor, retry_context=None).navigation
    revoked[0] = True
    with pytest.raises(RequestError, match="REQUEST_RECOVERY_REQUIRED"):
        desktop.composition._task_navigation.dispatch(intent)
    assert desktop.facade.active_plan_count == 0
    assert len(desktop.runtime.tasks.list(desktop.actor)) == 1


def test_missing_retained_result_fails_closed_without_opening_a_dialog(desktop):
    result = OperationResult(OperationOutcome.COMPLETED, counts=OperationCounts(succeeded=1))
    ref, item = _record(desktop, failed=False, result=result)
    assert item.available_actions.open_result
    actions = desktop.runtime.use_cases.resolve("task_center_actions")
    intent = actions.execute(item, TaskCenterAction.OPEN_RESULT, desktop.actor, retry_context=None).navigation
    desktop.results.clear()
    with pytest.raises(ValueError, match="当前不可用"):
        desktop.composition._task_navigation.dispatch(intent)
    with pytest.raises(TaskCenterActionError, match="不可用"):
        actions.execute(item, TaskCenterAction.OPEN_RESULT, desktop.actor, retry_context=None)
    assert not desktop.composition._task_navigation._dialogs
    refreshed = next(
        value for value in actions.list_history(desktop.actor, retry_context=None) if value.run_id == ref.run_id
    )
    assert not refreshed.available_actions.open_result


def test_shell_close_unregisters_production_navigation(desktop):
    result = OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic("WRITE_DENIED", "Output directory is read-only"),),
        counts=OperationCounts(failed=1),
    )
    ref, item = _record(desktop, result=result)
    assert item.available_actions.retry
    panel = _panel(desktop, ref.run_id)
    panel._open_result.click()
    _panel(desktop, ref.run_id)._open_log.click()
    dialogs = tuple(desktop.composition._task_navigation._dialogs)
    assert len(dialogs) == 2 and all(dialog.isVisible() for dialog in dialogs)
    desktop.composition.close()
    assert all(not dialog.isVisible() for dialog in dialogs)
    actions = desktop.runtime.use_cases.resolve("task_center_actions")
    refreshed = actions.list_history(desktop.actor, retry_context=None)[0]
    assert not refreshed.available_actions.retry
    assert not refreshed.available_actions.open_result


def test_ai_result_selects_requested_persisted_record_and_recovery_opens_real_history(desktop):
    ref, _item = _record(desktop, kind="ai-polish")
    store = TaskHistoryStore(desktop.directory / "ai-task-history")
    store.save({"run_id": "newer-unrelated", "created_at": "2026-10-13T00:00:00Z", "state": "completed", "sources": []})
    store.save({"run_id": ref.run_id, "created_at": "2026-10-12T00:00:00Z", "state": "failed", "sources": []})
    panel = _panel(desktop, ref.run_id)
    assert panel._open_result.isEnabled()
    panel._open_result.click()
    registry = desktop.host.workbench.ai_tasks
    assert len(registry.history_windows) == 1
    dialog = registry.history_windows[0]
    assert isinstance(dialog, TaskHistoryDialog)
    _until(lambda: not dialog.busy)
    assert dialog.tasks.currentItem().data(0, Qt.ItemDataRole.UserRole) == ref.run_id
    _until(lambda: bool(dialog._dialogs))
    assert dialog._dialogs[0].record["run_id"] == ref.run_id
    assert not panel._feature_recovery.isHidden()
    panel._feature_recovery.click()
    assert len(registry.history_windows) == 2
    assert isinstance(registry.history_windows[-1], TaskHistoryDialog)
    _until(lambda: not registry.busy)
    assert registry.history_windows[-1].tasks.topLevelItemCount() == 2


def test_ai_result_payload_lost_after_metadata_save_shows_actionable_error(desktop):
    ref, _item = _record(desktop, kind="ai-polish")
    store = TaskHistoryStore(desktop.directory / "ai-task-history")
    payload = store.save({"run_id": ref.run_id, "created_at": "2026-10-12T00:00:00Z", "state": "failed", "sources": []})
    # Remove only the test-created payload; its retained metadata reproduces
    # an interrupted or externally removed feature history artifact.
    payload.unlink()
    panel = _panel(desktop, ref.run_id)
    panel._open_result.click()
    dialog = desktop.host.workbench.ai_tasks.history_windows[-1]
    _until(lambda: "读取失败" in dialog.status.text())
    assert not dialog._dialogs
    assert not desktop.output.exists()
