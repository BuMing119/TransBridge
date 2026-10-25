from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.contracts.paratranz.test_sync_execution import (
    NAMESPACE,
    ControlledRemote,
    _executor,
    _local,
    _request,
    _TaskClock,
    _TaskIds,
)
from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.application.io.identity import EntryKey
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.security.diagnostic_logging import DiagnosticLogFormatter
from transbridge.application.sync import (
    AuthorizedSyncPlan,
    ConflictPolicy,
    ParaTranzSyncTaskDraft,
    ParaTranzSyncTaskEntrypoint,
    SyncOperation,
    SyncPlanner,
)
from transbridge.application.sync.execution_diagnostics import SyncExecutionDiagnostics
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskCancelled, TaskRuntime
from transbridge.ui.operations.sync_result import sync_receipt


def _events(caplog):
    return [
        json.loads(record.getMessage()) for record in caplog.records if record.name.endswith("execution_diagnostics")
    ]


def _task(*, preparation=None, recovery=None, cleanup=None, executor=None, runtime=None, on_result=None):
    local = (_local("a", "A"),)
    plan = SyncPlanner().plan(local, (), operation=SyncOperation.UPLOAD)
    runtime = runtime or TaskRuntime(
        id_generator=_TaskIds(),
        clock=_TaskClock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    executor = executor or MagicMock(execute=MagicMock(return_value=OperationResult.completed({})))
    owner = OwnerRef("owner-1", "gui")
    entrypoint = ParaTranzSyncTaskEntrypoint(runtime, executor, on_finished=cleanup, on_result=on_result)
    ref = entrypoint.submit(
        ParaTranzSyncTaskDraft(
            AuthorizedSyncPlan(plan, owner.owner_id, "NOT_REQUIRED"),
            7,
            NAMESPACE,
            local,
            preparation=preparation,
            recovery_snapshot=recovery,
        ),
        owner,
    )
    return runtime, entrypoint, ref, owner


@pytest.mark.parametrize("phase", ["preparation", "recovery_snapshot", "commit_permission", "execution", "cleanup"])
def test_task_phase_failures_produce_correlated_log_and_actionable_receipt(phase, caplog) -> None:
    caplog.set_level(logging.INFO)

    def fail(*_args):
        raise RuntimeError("controlled failure Authorization=private-test-token")

    closed = []
    delivered = []
    values = {"cleanup": lambda: closed.append(True), "on_result": lambda run_id, result: delivered.append(result)}
    if phase == "preparation":
        values["preparation"] = fail
    elif phase == "recovery_snapshot":
        values["recovery"] = fail
    elif phase == "commit_permission":
        runtime = TaskRuntime(
            id_generator=_TaskIds(),
            clock=_TaskClock(),
            backend=CallbackThreadBackend(lambda _run_id, target: target()),
        )
        runtime.commit_permit = fail
        values["runtime"] = runtime
    elif phase == "execution":
        values["executor"] = MagicMock(execute=MagicMock(side_effect=fail))
    else:
        values["cleanup"] = fail

    runtime, entrypoint, ref, owner = _task(**values)
    result = entrypoint.result(ref, owner)
    assert runtime.get(ref, owner).state is JobState.FAILED
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == f"SYNC_{phase.upper()}_FAILED"
    assert delivered == [result]
    assert closed == ([] if phase == "cleanup" else [True])
    events = _events(caplog)
    failed = next(item for item in events if item["phase"] == phase and item["status"] == "failed")
    assert failed["run_id"] == ref.run_id
    assert failed["plan_id"]
    assert failed["error_type"] == "RuntimeError"
    assert "controlled failure" in failed["reason"]
    formatted = "\n".join(DiagnosticLogFormatter().format(record) for record in caplog.records)
    assert "Traceback" in formatted
    assert "private-test-token" not in formatted
    assert "private-test-token" not in repr(result.to_dict())
    activity = SimpleNamespace(state=JobState.FAILED, run_id=ref.run_id, diagnostic_refs=())
    receipt = sync_receipt(activity, result, action="更新工程译文")
    assert "未取得执行结果" not in receipt.text
    assert "controlled failure" in receipt.text


def test_cancellation_during_preparation_has_receipt_without_failure_traceback(caplog) -> None:
    caplog.set_level(logging.INFO)

    def cancelled(_run_id):
        raise TaskCancelled("cancelled during revalidation")

    runtime, entrypoint, ref, owner = _task(preparation=cancelled)
    assert runtime.get(ref, owner).state is JobState.CANCELLED
    assert entrypoint.result(ref, owner).outcome is OperationOutcome.CANCELLED
    events = _events(caplog)
    assert any(item["phase"] == "preparation" and item["status"] == "cancelled" for item in events)
    assert not any(record.exc_info for record in caplog.records)


def test_cleanup_failure_keeps_successful_writes_visible(caplog) -> None:
    caplog.set_level(logging.INFO)

    def fail():
        raise OSError("controlled cleanup fault")

    success = OperationResult.completed({"committed": True}, counts=OperationCounts(succeeded=2))
    runtime, entrypoint, ref, owner = _task(executor=MagicMock(execute=MagicMock(return_value=success)), cleanup=fail)
    result = entrypoint.result(ref, owner)
    assert runtime.get(ref, owner).state is JobState.FAILED
    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.succeeded == 2
    assert result.value == {"committed": True}
    assert result.diagnostics[0].code == "SYNC_CLEANUP_FAILED"


def test_snapshot_loading_fault_is_logged_without_claiming_changed_data(caplog) -> None:
    caplog.set_level(logging.INFO)
    local = [_local("a", "A")]
    remote = ControlledRemote()
    plan = SyncPlanner().plan(
        local, (), operation=SyncOperation.UPLOAD, scope=f"paratranz:project:7:source:{NAMESPACE.value}"
    )

    def broken_fetch(*_args, **_kwargs):
        raise ValueError("invalid payload structure")

    remote.fetch = broken_fetch
    result = _executor(remote, local).execute(_request(plan, local))
    assert result.diagnostics[0].code == "SYNC_SNAPSHOT_CHECK_FAILED"
    assert remote.calls == []
    event = next(item for item in _events(caplog) if item["status"] == "failed")
    assert event["phase"] == "remote_snapshot"
    assert event["reason"] == "invalid payload structure"


def test_changed_snapshot_is_still_blocked_and_logged(caplog) -> None:
    caplog.set_level(logging.INFO)
    local = [_local("a", "A")]
    remote = ControlledRemote()
    plan = SyncPlanner().plan(
        local, (), operation=SyncOperation.UPLOAD, scope=f"paratranz:project:7:source:{NAMESPACE.value}"
    )
    changed = [replace(local[0], translation="changed")]
    result = _executor(remote, changed).execute(_request(plan, changed))
    assert result.diagnostics[0].code == "STALE_SYNC_PLAN"
    assert remote.calls == []
    assert any(item.get("code") == "STALE_PLAN" for item in _events(caplog))


@pytest.mark.parametrize("original", ["private quest journal original body", 'private journal\nwith "quotes"'])
def test_qualified_identity_original_is_omitted_from_execution_failures_and_result_logs(original, caplog) -> None:
    caplog.set_level(logging.INFO)
    key = EntryKey(NAMESPACE, "Quest:02000001|88~QUST:CNAM", original)
    evidence = SyncExecutionDiagnostics("qualified-run", "plan-1", "upload", 7)
    try:
        raise ExternalServiceError(ExternalServiceCategory.CONFLICT, f"Rejected qualified entry {key.serialize()}")
    except ExternalServiceError as exc:
        evidence.failure("execution", exc, entry_key=key, item_id="item-1")
    result = OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(
            Diagnostic("REMOTE_CONFLICT", f"Failed entry {original}", details=(("entry_key", key.serialize()),)),
        ),
        counts=OperationCounts(failed=1),
        run_id="qualified-run",
    )
    evidence.result(result)
    messages = "\n".join(record.getMessage() for record in caplog.records)
    formatted = "\n".join(DiagnosticLogFormatter().format(record) for record in caplog.records)
    assert original not in messages
    assert original not in formatted
    assert json.dumps(original)[1:-1] not in messages
    assert "Traceback" in formatted
    expected = {
        "namespace": NAMESPACE.value,
        "local_key": key.local_key,
        "identity_original_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "identity_original_length": len(original),
    }
    assert [event["entry_key"] for event in _events(caplog) if event.get("entry_key")] == [expected, expected]
    assert result.diagnostics[0].details[0][1] == key.serialize()


def test_invalid_serialized_identity_is_not_echoed_in_result_log(caplog) -> None:
    caplog.set_level(logging.INFO)
    result = OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic("REMOTE_FAILED", "Write failed", details=(("entry_key", "private malformed body"),)),),
        counts=OperationCounts(failed=1),
    )
    SyncExecutionDiagnostics("malformed-run", "plan-1", "upload", 7).result(result)
    assert "private malformed body" not in "\n".join(record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("external_error", [True, False])
def test_executor_failure_uses_safe_qualified_key_projection(external_error, caplog) -> None:
    caplog.set_level(logging.INFO)
    original = "private original from qualified executor entry"
    key = EntryKey(NAMESPACE, "a", original)
    local = [replace(_local("a", "Local edit"), entry_key=key, original=original)]
    remote = ControlledRemote((ParaTranzEntry(7, "a", original, "Cloud", "context", 1),))
    plan = SyncPlanner().plan(
        local,
        remote.fetch(7, NAMESPACE, limit=100),
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )

    def fail(*_args, **_kwargs):
        if external_error:
            raise ExternalServiceError(ExternalServiceCategory.CONFLICT, f"Cannot write {original}")
        raise ValueError(f"Cannot write {original}")

    remote.update_entry_translation = fail
    result = _executor(remote, local).execute(_request(plan, local, confirmation="CONFIRMED"))
    SyncExecutionDiagnostics("run-1", plan.plan_id, "upload", 7).result(result)
    assert result.outcome is OperationOutcome.FAILED
    assert original not in "\n".join(DiagnosticLogFormatter().format(record) for record in caplog.records)
    failures = [event for event in _events(caplog) if event.get("phase") == "execution"]
    assert failures[0]["entry_key"]["identity_original_length"] == len(original)
