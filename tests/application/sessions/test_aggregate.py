from __future__ import annotations

from dataclasses import replace

from transbridge.application.contracts import JobRef
from transbridge.application.sessions import (
    ApprovalState,
    ControllerSnapshot,
    ControllerState,
    PendingApproval,
    RecoveryStatus,
    SessionAggregate,
    SessionEventKind,
    SessionJobRef,
    SessionRuntimeEvent,
    SessionSnapshot,
)
from transbridge.application.tasks.models import JobState, OwnerRef
from transbridge.persistence.v2 import ProjectId, SessionId, SessionRef, VariantId
from transbridge.persistence.v2.schema import validate_v2


def _snapshot(*, revision: int = 4, job_state: JobState = JobState.RUNNING) -> SessionSnapshot:
    ref = SessionRef(SessionId("session-a"))
    owner = OwnerRef(
        "owner",
        "gui",
        project_id="project-a",
        variant_id="variant-a",
        session_id=ref.identity.value,
    )
    return SessionSnapshot(
        ref=ref,
        name="会话 A",
        owner=owner,
        messages=({"role": "user", "content": "hello", "parts": [["pair", 1]]},),
        backend_history=({"role": "user", "content": "canonical"},),
        backend_summary="summary",
        controller=ControllerSnapshot(ControllerState.AWAITING_CONFIRM),
        project_id=ProjectId("project-a"),
        variant_id=VariantId("variant-a"),
        approvals=(
            PendingApproval(
                "approval-1",
                owner.owner_id,
                ref.identity.value,
                "run-1",
                "hash-1",
                revision,
                ApprovalState.PENDING,
            ),
        ),
        jobs=(SessionJobRef(JobRef("job-1", owner.owner_id, "run-1"), job_state),),
        revision=revision,
        created_at="2026-08-18T00:00:00Z",
        last_active_at="2026-08-18T00:01:00Z",
    )


def test_session_snapshot_roundtrip_preserves_full_backend_and_returns_defensive_messages() -> None:
    snapshot = _snapshot()
    dto = snapshot.to_dto()
    validated = validate_v2(dto.envelope.to_dict(), snapshot.ref)
    restored = SessionSnapshot.from_dto(validated, snapshot.ref)

    visible = restored.visible_messages()
    visible[0]["parts"][0][0] = "mutated"

    assert restored == snapshot
    assert restored.visible_messages()[0]["parts"] == [["pair", 1]]
    assert restored.backend_messages() == ({"role": "user", "content": "canonical"},)
    assert restored.recovery is RecoveryStatus.COMPLETE


def test_legacy_messages_only_load_is_explicitly_degraded() -> None:
    snapshot = _snapshot()
    dto = snapshot.to_dto()
    data = dto.envelope.data
    for key in ("owner", "controller", "history", "backend_summary"):
        data.pop(key)
    data["job_refs"] = [
        {
            "ref": {"job_id": "bad", "owner_id": "attacker", "run_id": "run"},
            "state": "running",
        }
    ]

    restored = SessionSnapshot.from_dto(dto, snapshot.ref)

    assert restored.visible_messages() == snapshot.visible_messages()
    assert restored.backend_messages() == ()
    assert restored.jobs == ()
    assert restored.recovery is RecoveryStatus.DEGRADED
    assert {
        "backend_history_missing",
        "controller_state_missing",
        "job_ref_dropped",
        "owner_scope_missing",
    }.issubset(restored.degradation_reasons)


def test_runtime_event_requires_exact_owner_run_revision_and_monotonic_sequence() -> None:
    aggregate = SessionAggregate(_snapshot())
    snapshot = aggregate.snapshot()
    event = SessionRuntimeEvent(
        SessionEventKind.JOB_STATE,
        snapshot.owner,
        "run-1",
        snapshot.revision,
        1,
        "job-1",
        JobState.COMPLETED,
    )

    applied = aggregate.apply_runtime_event(event)
    stale = aggregate.apply_runtime_event(event)
    wrong_owner = aggregate.apply_runtime_event(
        replace(
            event,
            owner=OwnerRef("other", "gui", session_id="session-a"),
            aggregate_revision=aggregate.revision,
            sequence=2,
        )
    )

    assert applied.applied
    assert aggregate.snapshot().jobs[0].state is JobState.COMPLETED
    assert stale.diagnostic is not None and stale.diagnostic.code == "SESSION_EVENT_REVISION_MISMATCH"
    assert wrong_owner.diagnostic is not None and wrong_owner.diagnostic.code == "SESSION_EVENT_OWNER_MISMATCH"


def test_terminal_job_event_cannot_regress_even_with_newer_sequence() -> None:
    aggregate = SessionAggregate(_snapshot(job_state=JobState.COMPLETED))
    snapshot = aggregate.snapshot()

    result = aggregate.apply_runtime_event(
        SessionRuntimeEvent(
            SessionEventKind.JOB_STATE,
            snapshot.owner,
            "run-1",
            snapshot.revision,
            2,
            "job-1",
            JobState.RUNNING,
        )
    )

    assert not result.applied
    assert result.diagnostic is not None
    assert result.diagnostic.code == "SESSION_EVENT_TERMINAL_REGRESSION"
    assert aggregate.revision == snapshot.revision


def test_approval_event_requires_exact_owner_run_revision_and_request_hash() -> None:
    aggregate = SessionAggregate(_snapshot())
    snapshot = aggregate.snapshot()
    wrong_hash = aggregate.apply_runtime_event(
        SessionRuntimeEvent(
            kind=SessionEventKind.APPROVAL,
            owner=snapshot.owner,
            run_id="run-1",
            aggregate_revision=snapshot.revision,
            sequence=1,
            approval_id="approval-1",
            approval_state=ApprovalState.APPROVED,
            request_hash="wrong",
        )
    )
    applied = aggregate.apply_runtime_event(
        SessionRuntimeEvent(
            kind=SessionEventKind.APPROVAL,
            owner=snapshot.owner,
            run_id="run-1",
            aggregate_revision=snapshot.revision,
            sequence=1,
            approval_id="approval-1",
            approval_state=ApprovalState.APPROVED,
            request_hash="hash-1",
        )
    )

    assert wrong_hash.diagnostic is not None
    assert wrong_hash.diagnostic.code == "SESSION_EVENT_APPROVAL_HASH_MISMATCH"
    assert applied.applied
    assert aggregate.snapshot().approvals[0].state is ApprovalState.APPROVED


def test_subscribe_unsubscribe_and_close_release_listeners() -> None:
    aggregate = SessionAggregate(_snapshot())
    calls: list[int] = []
    unsubscribe = aggregate.subscribe(lambda snapshot: calls.append(snapshot.revision))
    assert aggregate.listener_count == 1

    unsubscribe()
    unsubscribe()
    assert aggregate.listener_count == 0

    aggregate.subscribe(lambda snapshot: calls.append(snapshot.revision))
    aggregate.close()
    assert aggregate.listener_count == 0
