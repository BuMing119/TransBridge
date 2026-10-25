"""TaskRuntime entrypoint for already-authorized ParaTranz sync plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from transbridge.application.contracts import Diagnostic, ErrorCategory, JobRef, OperationOutcome, OperationResult
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.publish import TaskRuntimeCommitGuard
from transbridge.application.io.publish.guards import CommitDecision, PublishCommitGuard
from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    OwnerRef,
    TaskCancelled,
    TaskCleanupFailed,
    TaskRuntime,
)

from .execution_diagnostics import SyncExecutionDiagnostics, with_execution_failure
from .execution_models import RetryToken
from .executor import ExecuteSyncRequest, ParaTranzSyncExecutor
from .models import LocalEntrySnapshot, SyncOperation
from .use_case import AuthorizedSyncPlan


@dataclass(frozen=True, slots=True)
class ParaTranzSyncTaskPreparation:
    """Fresh authorization and local input produced inside the task worker."""

    authorized_plan: AuthorizedSyncPlan
    current_local_entries: tuple[LocalEntrySnapshot, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_local_entries", tuple(self.current_local_entries))


@dataclass(frozen=True, slots=True)
class ParaTranzSyncTaskDraft:
    authorized_plan: AuthorizedSyncPlan
    project_id: int
    namespace: SourceNamespace
    current_local_entries: tuple[LocalEntrySnapshot, ...]
    retry_token: RetryToken | None = None
    recovery_snapshot: Callable[[str], object] | None = None
    recovery_snapshot_name: str | None = None
    preparation: Callable[[str], ParaTranzSyncTaskPreparation] | None = None
    final_local_commit: bool = False

    def __post_init__(self) -> None:
        if self.project_id < 1:
            raise ValueError("ParaTranz task project id must be positive")
        if any(item.entry_key.namespace != self.namespace for item in self.current_local_entries):
            raise ValueError("ParaTranz task entries must share the requested namespace")
        if self.final_local_commit and self.authorized_plan.plan.operation is not SyncOperation.DOWNLOAD:
            raise ValueError("final local commit is only available for download tasks")


@dataclass(frozen=True, slots=True)
class _FinalLocalCommitGuard:
    guard: PublishCommitGuard
    runtime: TaskRuntime
    ref: JobRef
    owner: OwnerRef

    def commit(self, run_id: str, mutation: Callable[[], None]) -> CommitDecision:
        def publish_and_finish():
            try:
                mutation()
            except BaseException as exc:
                outcome = OperationOutcome.CANCELLED if isinstance(exc, TaskCancelled) else OperationOutcome.FAILED
                if not self.runtime.get(self.ref, self.owner).is_terminal:
                    self.runtime.update_progress(self.ref, self.owner, {"operation_outcome": outcome.value})
                raise
            self.runtime.update_progress(self.ref, self.owner, {"operation_outcome": OperationOutcome.COMPLETED.value})
            self.runtime.complete(self.ref, self.owner)

        return self.guard.commit(run_id, publish_and_finish)


class ParaTranzSyncTaskFailed(RuntimeError):
    def __init__(self, result: OperationResult) -> None:
        self.result = result
        super().__init__(",".join(item.code for item in result.diagnostics) or "ParaTranz sync failed")


class ParaTranzSyncTaskEntrypoint:
    """The executor retains sync semantics; this adapter owns only task lifecycle."""

    def __init__(
        self,
        runtime: TaskRuntime,
        executor: ParaTranzSyncExecutor,
        *,
        on_finished: Callable[[], None] | None = None,
        on_result: Callable[[str, OperationResult], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._executor = executor
        self._on_finished = on_finished
        self._on_result = on_result
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
                    ("recovery_snapshot", draft.recovery_snapshot_name or ""),
                    *((("await_result_receipt", "true"),) if draft.final_local_commit else ()),
                ),
            ),
            owner,
        )
        ref = deferred.ref

        def execute(cancellation) -> None:
            evidence = SyncExecutionDiagnostics(ref.run_id, plan.plan_id, plan.operation.value, draft.project_id)
            result = None
            error = None
            phase = "preparation"
            try:
                evidence.phase(phase, "started")
                authorized_plan = draft.authorized_plan
                current_local_entries = draft.current_local_entries
                if draft.preparation is not None:
                    cancellation.raise_if_cancelled()
                    prepared = draft.preparation(ref.run_id)
                    if not isinstance(prepared, ParaTranzSyncTaskPreparation):
                        raise TypeError("ParaTranz task preparation returned an invalid result")
                    if prepared.authorized_plan.owner_id != owner.owner_id:
                        raise PermissionError("prepared sync plan belongs to a different owner")
                    if prepared.authorized_plan.plan.plan_hash != plan.plan_hash:
                        raise ValueError("prepared sync plan differs from the submitted plan")
                    if any(item.entry_key.namespace != draft.namespace for item in prepared.current_local_entries):
                        raise ValueError("prepared sync entries do not share the requested namespace")
                    authorized_plan = prepared.authorized_plan
                    current_local_entries = prepared.current_local_entries
                    cancellation.raise_if_cancelled()
                evidence.phase(phase, "completed")
                if draft.recovery_snapshot is not None:
                    phase = "recovery_snapshot"
                    evidence.phase(phase, "started")
                    cancellation.raise_if_cancelled()
                    draft.recovery_snapshot(ref.run_id)
                    cancellation.raise_if_cancelled()
                    evidence.phase(phase, "completed")
                phase = "commit_permission"
                evidence.phase(phase, "started")
                permit = self._runtime.commit_permit(ref, owner)
                evidence.phase(phase, "completed")
                request = ExecuteSyncRequest(
                    authorized_plan,
                    draft.project_id,
                    draft.namespace,
                    current_local_entries,
                    ref.run_id,
                    _FinalLocalCommitGuard(TaskRuntimeCommitGuard(self._runtime, permit), self._runtime, ref, owner)
                    if draft.final_local_commit
                    else TaskRuntimeCommitGuard(self._runtime, permit),
                    cancellation=cancellation,
                    retry_token=draft.retry_token,
                )
                phase = "execution"
                evidence.phase(phase, "started")
                result = self._executor.execute(request)
                if not isinstance(result, OperationResult):
                    result = None
                    raise TypeError("ParaTranz executor returned an invalid result")
                evidence.phase(phase, "completed")
            except Exception as exc:
                error = exc
                cancelled = not isinstance(exc, TaskCleanupFailed) and (
                    cancellation.is_cancelled
                    or isinstance(exc, TaskCancelled)
                    or isinstance(exc, ExternalServiceError)
                    and exc.category.value == "cancelled"
                )
                if cancelled:
                    evidence.phase(phase, "cancelled")
                    result = OperationResult.cancelled(
                        Diagnostic("SYNC_CANCELLED", "同步已取消。", category=ErrorCategory.CANCELLED),
                        run_id=ref.run_id,
                    )
                else:
                    result = with_execution_failure(result, evidence.failure(phase, exc), run_id=ref.run_id)
            finally:
                if self._on_finished is not None:
                    phase = "cleanup"
                    evidence.phase(phase, "started")
                    try:
                        self._on_finished()
                    except Exception as exc:
                        result = with_execution_failure(result, evidence.failure(phase, exc), run_id=ref.run_id)
                        error = TaskCleanupFailed("ParaTranz resource cleanup failed; see execution log")
                        error.__cause__ = exc
                    else:
                        evidence.phase(phase, "completed")

            self._store_result(ref.run_id, result)
            try:
                if self._on_result is not None:
                    self._on_result(ref.run_id, result)
                if not self._runtime.get(ref, owner).is_terminal:
                    self._runtime.update_progress(ref, owner, {"operation_outcome": result.outcome.value})
            except Exception as exc:
                result = with_execution_failure(result, evidence.failure("result_publication", exc), run_id=ref.run_id)
                self._store_result(ref.run_id, result)
                error = error or exc
            evidence.result(result)
            if isinstance(error, TaskCleanupFailed):
                raise error
            if result.outcome is OperationOutcome.CANCELLED:
                raise TaskCancelled("ParaTranz synchronization cancelled") from error
            if result.outcome is OperationOutcome.FAILED or error is not None:
                raise ParaTranzSyncTaskFailed(result) from error

        self._runtime.schedule(ref, owner, execute)
        return ref

    def _store_result(self, run_id: str, result: OperationResult) -> None:
        with self._lock:
            self._results[run_id] = result
            while len(self._results) > 100:
                self._results.pop(next(iter(self._results)))

    def result(self, ref: JobRef, actor: OwnerRef) -> OperationResult[dict] | None:
        self._runtime.get(ref, actor)
        with self._lock:
            return self._results.get(ref.run_id)
