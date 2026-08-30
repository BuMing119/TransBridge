"""TaskRuntime lifecycle adapter for authorized terminology backup plans."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobCapabilities, JobSpec, OwnerRef, TaskCancelled, TaskRuntime

from .execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemStatus,
    TerminologySyncRetryToken,
)
from .executor import ExecuteTerminologyBackupRequest, TerminologyBackupExecutor
from .plan_models import TerminologySyncMode
from .use_case import AuthorizedTerminologySyncPlan


@dataclass(frozen=True, slots=True)
class TerminologySyncTaskDraft:
    authorized_plan: AuthorizedTerminologySyncPlan
    retry_token: TerminologySyncRetryToken | None = None


class TerminologySyncTaskIncomplete(RuntimeError):
    def __init__(self, result: TerminologyBackupExecutionResult) -> None:
        self.result = result
        codes = ",".join(item.code for item in result.outcomes if item.status is not TerminologySyncItemStatus.SKIPPED)
        super().__init__(codes or "terminology synchronization incomplete")


class TerminologySyncTaskEntrypoint:
    def __init__(self, runtime: TaskRuntime, executor: TerminologyBackupExecutor) -> None:
        self._runtime = runtime
        self._executor = executor
        self._results: dict[str, TerminologyBackupExecutionResult] = {}
        self._lock = RLock()

    def submit(self, draft: TerminologySyncTaskDraft, owner: OwnerRef) -> JobRef:
        authorized = draft.authorized_plan
        plan = authorized.plan
        if plan.mode not in {TerminologySyncMode.BACKUP, TerminologySyncMode.BIDIRECTIONAL}:
            raise ValueError("terminology sync task entrypoint received an unsupported mode")
        if authorized.owner_id != owner.owner_id:
            raise PermissionError("authorized terminology sync plan belongs to another owner")
        deferred = self._runtime.submit(
            JobSpec(
                job_type=f"operation.terminology_sync.{plan.mode.value}",
                input_ref=plan.line_id,
                input_fingerprint=plan.plan_hash,
                display_name="备份项目术语" if plan.mode.value == "backup" else "双向同步项目术语",
                capabilities=JobCapabilities(supports_cancel=True),
                metadata=(
                    ("mode", plan.mode.value),
                    ("plan_id", plan.plan_id),
                    ("line_id", plan.line_id),
                ),
            ),
            owner,
        )
        ref = deferred.ref

        def execute(cancellation) -> None:
            result = self._executor.execute(
                ExecuteTerminologyBackupRequest(
                    authorized,
                    ref.run_id,
                    cancellation=cancellation,
                    retry_token=draft.retry_token,
                )
            )
            with self._lock:
                self._results[ref.run_id] = result
                while len(self._results) > 100:
                    self._results.pop(next(iter(self._results)))
            statuses = {item.status for item in result.outcomes}
            if statuses == {TerminologySyncItemStatus.CANCELLED}:
                raise TaskCancelled("terminology synchronization cancelled")
            if result.partial or result.reconcile_required:
                raise TerminologySyncTaskIncomplete(result)

        self._runtime.schedule(ref, owner, execute)
        return ref

    def reconcile(self, draft: TerminologySyncTaskDraft, owner: OwnerRef) -> JobRef:
        """Schedule evidence-based reconciliation of unknown remote outcomes."""

        authorized = draft.authorized_plan
        plan = authorized.plan
        token = draft.retry_token
        if plan.mode not in {TerminologySyncMode.BACKUP, TerminologySyncMode.BIDIRECTIONAL}:
            raise ValueError("terminology sync task entrypoint received an unsupported reconcile mode")
        if authorized.owner_id != owner.owner_id:
            raise PermissionError("authorized terminology sync plan belongs to another owner")
        if token is None or not token.unknown_item_ids:
            raise ValueError("reconcile requires a retry token containing unknown items")
        deferred = self._runtime.submit(
            JobSpec(
                job_type="operation.terminology_sync.reconcile",
                input_ref=plan.line_id,
                input_fingerprint=token.token_digest,
                display_name="核对术语同步未知结果",
                capabilities=JobCapabilities(supports_cancel=True),
                metadata=(("plan_id", plan.plan_id), ("line_id", plan.line_id)),
            ),
            owner,
        )
        ref = deferred.ref

        def execute(cancellation) -> None:
            result = self._executor.reconcile(
                ExecuteTerminologyBackupRequest(
                    authorized,
                    ref.run_id,
                    cancellation=cancellation,
                    retry_token=token,
                )
            )
            with self._lock:
                self._results[ref.run_id] = result
                while len(self._results) > 100:
                    self._results.pop(next(iter(self._results)))
            if result.partial or result.reconcile_required:
                raise TerminologySyncTaskIncomplete(result)

        self._runtime.schedule(ref, owner, execute)
        return ref

    def result(self, ref: JobRef, actor: OwnerRef) -> TerminologyBackupExecutionResult | None:
        self._runtime.get(ref, actor)
        with self._lock:
            return self._results.get(ref.run_id)


__all__ = [
    "TerminologySyncTaskDraft",
    "TerminologySyncTaskEntrypoint",
    "TerminologySyncTaskIncomplete",
]
