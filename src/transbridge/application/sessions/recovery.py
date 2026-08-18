"""Session reference, approval, task, and controller recovery reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks.models import JobState, OwnerRef

from .models import (
    ApprovalState,
    ControllerSnapshot,
    ControllerState,
    RecoveryStatus,
    SessionJobRef,
    SessionSnapshot,
)

ReferenceResolver = Callable[[str | None, str | None], bool]
TaskResolver = Callable[[SessionJobRef, OwnerRef], JobState | None]


class SessionRecoveryReconciler:
    def __init__(
        self,
        *,
        reference_resolver: ReferenceResolver | None = None,
        task_resolver: TaskResolver | None = None,
    ) -> None:
        self._reference_resolver = reference_resolver
        self._task_resolver = task_resolver

    def reconcile(self, snapshot: SessionSnapshot, context: RequestContext) -> SessionSnapshot:
        reasons = set(snapshot.degradation_reasons)
        project_id = None if snapshot.project_id is None else snapshot.project_id.value
        variant_id = None if snapshot.variant_id is None else snapshot.variant_id.value
        if self._reference_resolver is not None and not self._reference_resolver(project_id, variant_id):
            reasons.add("active_project_variant_reference_unavailable")

        approvals = []
        for approval in snapshot.approvals:
            if approval.state is ApprovalState.PENDING and approval.aggregate_revision != snapshot.revision:
                reasons.add("stale_pending_approval_dropped")
                continue
            approvals.append(approval)

        jobs: list[SessionJobRef] = []
        for job in snapshot.jobs:
            if self._task_resolver is None:
                jobs.append(job)
                continue
            state = self._task_resolver(job, snapshot.owner)
            if state is None:
                jobs.append(
                    replace(
                        job,
                        recoverable=False,
                        reason="task_runtime_job_unavailable",
                    )
                )
                reasons.add("task_runtime_job_unavailable")
            else:
                jobs.append(replace(job, state=state, recoverable=True, reason=None))

        controller = snapshot.controller
        if controller.state in {
            ControllerState.THINKING,
            ControllerState.EXECUTING,
            ControllerState.AWAITING_TASK,
        } and not any(job.recoverable for job in jobs):
            controller = ControllerSnapshot(
                ControllerState.IDLE,
                0,
                controller.auto_mode,
                False,
                "in_flight_controller_state_has_no_recoverable_job",
            )
            reasons.add("controller_reset_to_idle")
        return replace(
            snapshot,
            approvals=tuple(approvals),
            jobs=tuple(jobs),
            controller=controller,
            recovery=RecoveryStatus.DEGRADED if reasons else RecoveryStatus.COMPLETE,
            degradation_reasons=tuple(reasons),
        )


__all__ = ["ReferenceResolver", "SessionRecoveryReconciler", "TaskResolver"]
