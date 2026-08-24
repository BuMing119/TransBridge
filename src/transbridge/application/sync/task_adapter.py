"""TaskRuntime entrypoint for already-authorized ParaTranz sync plans."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from transbridge.application.contracts import JobRef, OperationOutcome, OperationResult
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.publish import TaskRuntimeCommitGuard
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
)

from .execution_models import RetryToken
from .executor import ExecuteSyncRequest, ParaTranzSyncExecutor
from .models import LocalEntrySnapshot
from .use_case import AuthorizedSyncPlan


@dataclass(frozen=True, slots=True)
class ParaTranzSyncTaskDraft:
    authorized_plan: AuthorizedSyncPlan
    project_id: int
    namespace: SourceNamespace
    current_local_entries: tuple[LocalEntrySnapshot, ...]
    retry_token: RetryToken | None = None

    def __post_init__(self) -> None:
        if self.project_id < 1:
            raise ValueError("ParaTranz task project id must be positive")
        if any(item.entry_key.namespace != self.namespace for item in self.current_local_entries):
            raise ValueError("ParaTranz task entries must share the requested namespace")


class ParaTranzSyncTaskFailed(RuntimeError):
    def __init__(self, result: OperationResult) -> None:
        self.result = result
        super().__init__(",".join(item.code for item in result.diagnostics) or "ParaTranz sync failed")


class ParaTranzSyncTaskEntrypoint:
    """The executor retains sync semantics; this adapter owns only task lifecycle."""

    def __init__(self, runtime: TaskRuntime, executor: ParaTranzSyncExecutor) -> None:
        self._runtime = runtime
        self._executor = executor
        self._results: dict[str, OperationResult[dict]] = {}
        self._lock = RLock()

    def submit(self, draft: ParaTranzSyncTaskDraft, owner: OwnerRef) -> JobRef:
        plan = draft.authorized_plan.plan
        if draft.authorized_plan.owner_id != owner.owner_id:
            raise PermissionError("authorized sync plan belongs to a different owner")
        deferred = self._runtime.submit(
            JobSpec(
                job_type=f"operation.paratranz.{plan.operation.value}",
                input_ref=plan.scope,
                input_fingerprint=plan.plan_hash,
                display_name="上传到 ParaTranz" if plan.operation.value == "upload" else "从 ParaTranz 下载",
                capabilities=JobCapabilities(supports_cancel=True),
                metadata=(
                    ("operation", plan.operation.value),
                    ("plan_id", plan.plan_id),
                ),
            ),
            owner,
        )
        ref = deferred.ref

        def execute(cancellation) -> None:
            permit = self._runtime.commit_permit(ref, owner)
            request = ExecuteSyncRequest(
                draft.authorized_plan,
                draft.project_id,
                draft.namespace,
                draft.current_local_entries,
                ref.run_id,
                TaskRuntimeCommitGuard(self._runtime, permit),
                cancellation=cancellation,
                retry_token=draft.retry_token,
            )
            result = self._executor.execute(request)
            with self._lock:
                self._results[ref.run_id] = result
                while len(self._results) > 100:
                    self._results.pop(next(iter(self._results)))
            if result.outcome is OperationOutcome.CANCELLED:
                raise TaskCancelled("ParaTranz synchronization cancelled")
            if result.outcome is OperationOutcome.FAILED:
                raise ParaTranzSyncTaskFailed(result)

        self._runtime.schedule(ref, owner, execute)
        return ref

    def result(self, ref: JobRef, actor: OwnerRef) -> OperationResult[dict] | None:
        self._runtime.get(ref, actor)
        with self._lock:
            return self._results.get(ref.run_id)
