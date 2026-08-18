from __future__ import annotations

from dataclasses import replace

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.sessions import (
    ApprovalState,
    ControllerSnapshot,
    ControllerState,
    PendingApproval,
    RecoveryStatus,
    SessionJobRef,
    SessionRecoveryReconciler,
    SessionSnapshot,
)
from transbridge.application.tasks.models import JobState, OwnerRef
from transbridge.persistence.v2 import ProjectId, SessionId, SessionRef, VariantId


def _snapshot(*, revision: int = 3) -> SessionSnapshot:
    ref = SessionRef(SessionId("session-a"))
    owner = OwnerRef("owner", "gui", "project-a", "variant-a", ref.identity.value)
    return SessionSnapshot(
        ref=ref,
        name="session",
        owner=owner,
        messages=({"role": "user", "content": "visible"},),
        backend_history=({"role": "user", "content": "backend"},),
        backend_summary=None,
        controller=ControllerSnapshot(ControllerState.AWAITING_TASK, recoverable=False, reason="in-flight"),
        project_id=ProjectId("project-a"),
        variant_id=VariantId("variant-a"),
        approvals=(
            PendingApproval(
                "approval-current",
                owner.owner_id,
                ref.identity.value,
                "run-1",
                "hash",
                revision,
            ),
            PendingApproval(
                "approval-stale",
                owner.owner_id,
                ref.identity.value,
                "run-0",
                "old-hash",
                revision - 1,
            ),
        ),
        jobs=(SessionJobRef(JobRef("job-1", owner.owner_id, "run-1"), JobState.RUNNING),),
        revision=revision,
        created_at="2026-08-18T00:00:00Z",
        last_active_at="2026-08-18T00:01:00Z",
    )


def test_reconcile_missing_task_preserves_visible_conversation_and_marks_degraded() -> None:
    snapshot = _snapshot()
    reconciled = SessionRecoveryReconciler(task_resolver=lambda job, owner: None).reconcile(
        snapshot,
        RequestContext("owner", session_id="session-a"),
    )

    assert reconciled.visible_messages() == snapshot.visible_messages()
    assert reconciled.backend_messages() == snapshot.backend_messages()
    assert reconciled.jobs[0].recoverable is False
    assert reconciled.controller.state is ControllerState.IDLE
    assert reconciled.recovery is RecoveryStatus.DEGRADED
    assert "task_runtime_job_unavailable" in reconciled.degradation_reasons
    assert "controller_reset_to_idle" in reconciled.degradation_reasons


def test_reconcile_restores_exact_task_state_and_drops_only_stale_approval() -> None:
    reconciled = SessionRecoveryReconciler(
        reference_resolver=lambda project, variant: (project, variant) == ("project-a", "variant-a"),
        task_resolver=lambda job, owner: JobState.PAUSED,
    ).reconcile(_snapshot(), RequestContext("owner", session_id="session-a"))

    assert reconciled.jobs[0].state is JobState.PAUSED
    assert reconciled.jobs[0].recoverable
    assert tuple(item.approval_id for item in reconciled.approvals) == ("approval-current",)
    assert reconciled.approvals[0].state is ApprovalState.PENDING
    assert "stale_pending_approval_dropped" in reconciled.degradation_reasons


def test_unavailable_project_variant_reference_is_degraded_without_rewriting_ids() -> None:
    snapshot = replace(_snapshot(), controller=ControllerSnapshot(), jobs=(), approvals=())
    reconciled = SessionRecoveryReconciler(reference_resolver=lambda project, variant: False).reconcile(
        snapshot,
        RequestContext("owner", session_id="session-a"),
    )

    assert reconciled.project_id == snapshot.project_id
    assert reconciled.variant_id == snapshot.variant_id
    assert reconciled.recovery is RecoveryStatus.DEGRADED
    assert "active_project_variant_reference_unavailable" in reconciled.degradation_reasons
