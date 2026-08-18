"""AWAITING_TASK production path: bridge submit, terminal outcomes, session gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from transbridge.application.contracts import Deferred, JobRef, OperationOutcome
from transbridge.application.tasks import (
    JobCapabilities,
    JobEventType,
    JobSpec,
    JobState,
    OwnerRef,
    RuntimeTaskBridge,
    SessionJobGate,
    TaskRuntime,
    TaskWaitTimeout,
)


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"bridge-run-{self.value}"


class AdvancingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 18, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


def _runtime() -> TaskRuntime:
    return TaskRuntime(id_generator=SequenceIds(), clock=AdvancingClock())


def _owner(*, session_id: str | None = "session-1", entrypoint: str = "gui") -> OwnerRef:
    return OwnerRef(
        owner_id="owner-1",
        entrypoint=entrypoint,
        project_id="project-1",
        session_id=session_id,
    )


def _spec(job_type: str = "translation") -> JobSpec:
    return JobSpec(
        job_type=job_type,
        input_ref="variant:one",
        input_fingerprint="sha256:abc",
        capabilities=JobCapabilities(supports_pause=True, supports_resume=True, supports_cancel=True),
    )


def test_bridge_submit_returns_deferred_jobref_and_terminal_operation_result() -> None:
    runtime = _runtime()
    bridge = RuntimeTaskBridge(runtime)
    owner = _owner()

    def workload(cancellation):
        return "done"

    deferred = bridge.submit(_spec(), owner, workload)

    assert isinstance(deferred, Deferred)
    assert isinstance(deferred.ref, JobRef)
    assert deferred.ref.run_id == deferred.ref.job_id

    terminal = bridge.wait_terminal(deferred.ref, owner, timeout=5.0)
    assert terminal.snapshot.state is JobState.COMPLETED
    assert terminal.outcome is OperationOutcome.COMPLETED

    result = bridge.to_operation_result(terminal.snapshot)
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value is not None
    assert result.value.ref.run_id == deferred.ref.run_id
    assert result.counts.succeeded == 1


def test_bridge_workload_failure_maps_to_failed_and_no_terminal_value_mutation() -> None:
    runtime = _runtime()
    bridge = RuntimeTaskBridge(runtime)
    owner = _owner()

    def workload(cancellation):
        raise RuntimeError("boom")

    deferred = bridge.submit(_spec(), owner, workload)
    terminal = bridge.wait_terminal(deferred.ref, owner, timeout=5.0)

    assert terminal.snapshot.state is JobState.FAILED
    assert terminal.outcome is OperationOutcome.FAILED
    result = bridge.to_operation_result(terminal.snapshot)
    assert result.outcome is OperationOutcome.FAILED
    assert result.value is None
    assert result.counts.failed == 1


def test_cancel_before_start_maps_to_cancelled_outcome() -> None:
    runtime = _runtime()
    bridge = RuntimeTaskBridge(runtime)
    owner = _owner()

    deferred = runtime.submit(_spec(), owner)
    runtime.cancel(deferred.ref, owner)
    terminal = bridge.wait_terminal(deferred.ref, owner, timeout=5.0)

    assert terminal.snapshot.state is JobState.CANCELLED
    assert terminal.outcome is OperationOutcome.CANCELLED
    assert any(d.code == "JOB_CANCELLED" for d in terminal.diagnostics)


def test_wait_timeout_is_not_reported_as_a_cancelled_terminal_outcome() -> None:
    runtime = _runtime()
    bridge = RuntimeTaskBridge(runtime)
    owner = _owner()
    ref = runtime.submit(_spec(), owner).ref

    with pytest.raises(TaskWaitTimeout) as caught:
        bridge.wait_terminal(ref, owner, timeout=0.0)

    assert caught.value.snapshot.state is JobState.QUEUED
    assert runtime.get(ref, owner).state is JobState.QUEUED


def test_session_gate_accepts_active_session_and_audits_stale_events() -> None:
    runtime = _runtime()
    gate = SessionJobGate(runtime)
    active_owner = _owner(session_id="session-1")
    stale_owner = _owner(session_id="session-old")
    bridge = RuntimeTaskBridge(runtime)

    active_ref = bridge.submit(_spec(), active_owner, lambda cancellation: None).ref
    stale_ref = bridge.submit(_spec(), stale_owner, lambda cancellation: None).ref

    # Terminal events flow through the runtime subscription.
    gate.activate("session-1")
    gate.subscribe()

    terminal_active = bridge.wait_terminal(active_ref, active_owner, timeout=5.0)
    terminal_stale = bridge.wait_terminal(stale_ref, stale_owner, timeout=5.0)
    assert terminal_active.snapshot.state is JobState.COMPLETED
    assert terminal_stale.snapshot.state is JobState.COMPLETED

    accepted, audited = gate.accept_event(_finished_event(terminal_active.snapshot))
    assert accepted is True and audited is False

    accepted, audited = gate.accept_event(_finished_event(terminal_stale.snapshot))
    assert accepted is False and audited is True
    assert any(record["session_id"] == "session-old" for record in gate.audited())

    gate.close()


def test_session_gate_never_mutates_runtime_state() -> None:
    runtime = _runtime()
    gate = SessionJobGate(runtime)
    owner = _owner(session_id="session-1")
    ref = runtime.submit(_spec(), owner).ref
    runtime.start(ref, owner)
    revision_before = runtime.get(ref, owner).revision

    gate.activate("session-1")
    # Only runtime controls may change state; the gate has no write path.
    assert runtime.get(ref, owner).revision == revision_before


def test_session_gate_rejects_terminal_events_until_a_session_is_active() -> None:
    runtime = _runtime()
    gate = SessionJobGate(runtime)
    owner = _owner(session_id="session-1")
    ref = runtime.submit(_spec(), owner).ref
    runtime.cancel(ref, owner)

    accepted, audited = gate.accept_event(_finished_event(runtime.get(ref, owner)))

    assert accepted is False
    assert audited is True


def _finished_event(snapshot):
    from transbridge.application.tasks import JobEvent

    return JobEvent(
        event_type=JobEventType.FINISHED,
        snapshot=snapshot,
        sequence=snapshot.sequence,
        revision=snapshot.revision,
        occurred_at=datetime.now(UTC),
        previous_state=JobState.RUNNING,
    )
