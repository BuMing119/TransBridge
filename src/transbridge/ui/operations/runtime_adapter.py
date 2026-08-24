"""TaskRuntime-owned execution for confirmed operation plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from transbridge.application.contracts import JobRef, OperationOutcome, OperationResult
from transbridge.application.io.publish import TaskRuntimeCommitGuard
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
)

from .plan_view import OperationKind
from .preflight_view import (
    OperationObjectResult,
    OperationObjectStatus,
    OperationPreflightResult,
    OperationResultActionState,
)


class OperationTaskFailed(RuntimeError):
    def __init__(self, result: OperationResult) -> None:
        self.result = result
        super().__init__(",".join(item.code for item in result.diagnostics) or "operation failed")


@dataclass(frozen=True, slots=True)
class OperationRunContext:
    runtime: TaskRuntime
    ref: JobRef
    owner: OwnerRef
    cancellation: object

    def publish_commit_guard(self) -> TaskRuntimeCommitGuard:
        """Issue a revision-scoped permit; cancellation invalidates it."""
        return TaskRuntimeCommitGuard(
            self.runtime,
            self.runtime.commit_permit(self.ref, self.owner),
        )


class OperationWorkload(Protocol):
    def __call__(self, context: OperationRunContext) -> OperationResult: ...


@dataclass(frozen=True, slots=True)
class OperationTaskRequest:
    kind: OperationKind
    request_digest: str
    input_ref: str
    display_name: str
    workload: OperationWorkload
    supports_cancel: bool
    object_refs: tuple[str, ...] = ()
    failed_subset_factory: Callable[[tuple[str, ...]], OperationTaskRequest] | None = None

    def __post_init__(self) -> None:
        if len(self.request_digest) != 64:
            raise ValueError("operation task request requires a SHA-256 request digest")
        if not self.input_ref.strip() or not self.display_name.strip():
            raise ValueError("operation task input_ref and display_name must not be empty")


class OperationTaskAdapter:
    """Keeps terminal authority in TaskRuntime and result payloads in a bounded port."""

    def __init__(self, runtime: TaskRuntime, *, max_results: int = 100) -> None:
        if max_results < 1:
            raise ValueError("max_results must be positive")
        self._runtime = runtime
        self._max_results = max_results
        self._requests: dict[str, OperationTaskRequest] = {}
        self._results: dict[str, OperationResult] = {}
        self._owners: dict[str, OwnerRef] = {}
        self._order: list[str] = []
        self._lock = RLock()

    def submit(self, request: OperationTaskRequest, owner: OwnerRef) -> JobRef:
        specification = JobSpec(
            job_type=f"operation.{request.kind.value}",
            input_ref=request.input_ref,
            input_fingerprint=request.request_digest,
            display_name=request.display_name,
            capabilities=JobCapabilities(supports_cancel=request.supports_cancel),
            metadata=(("operation_kind", request.kind.value),),
        )
        deferred = self._runtime.submit(specification, owner)
        ref = deferred.ref
        with self._lock:
            self._requests[ref.run_id] = request
            self._owners[ref.run_id] = owner

        def execute(cancellation) -> None:
            context = OperationRunContext(self._runtime, ref, owner, cancellation)
            result = request.workload(context)
            with self._lock:
                self._results[ref.run_id] = result
                self._order.append(ref.run_id)
                self._trim()
            if result.outcome is OperationOutcome.CANCELLED:
                raise TaskCancelled("operation cancelled before formal side effect")
            if result.outcome is OperationOutcome.FAILED:
                raise OperationTaskFailed(result)

        self._runtime.schedule(ref, owner, execute)
        return ref

    def result(self, ref: JobRef, actor: OwnerRef) -> OperationResult | None:
        self._runtime.get(ref, actor)
        with self._lock:
            return self._results.get(ref.run_id)

    def result_state(self, ref: JobRef, actor: OwnerRef) -> OperationResultActionState | None:
        result = self.result(ref, actor)
        if result is None:
            return None
        with self._lock:
            request = self._requests[ref.run_id]
        objects = _object_results(result, request.object_refs)
        failed = tuple(item for item in objects if item.status is OperationObjectStatus.FAILED)
        retryable = (
            bool(failed) and request.failed_subset_factory is not None and all(item.retryable for item in failed)
        )
        return OperationResultActionState(
            run_id=ref.run_id,
            kind=request.kind,
            objects=objects,
            artifact_refs=tuple(result.artifact_refs),
            retry_failed_enabled=retryable,
            retry_disabled_reason="" if retryable else "失败项不具备安全重试工厂",
        )

    def retry_failed(
        self,
        previous: JobRef,
        actor: OwnerRef,
        *,
        re_preflight: Callable[[OperationTaskRequest, tuple[str, ...]], OperationPreflightResult],
    ) -> JobRef:
        state = self.result_state(previous, actor)
        if state is None or not state.retry_failed_enabled:
            raise ValueError("previous run has no retryable failed subset")
        with self._lock:
            request = self._requests[previous.run_id]
            owner = self._owners[previous.run_id]
        failed_refs = state.failed_refs
        # Feature-owned preflight must refresh credential/permission/revision
        # and idempotency evidence before the new immutable request is built.
        refreshed = re_preflight(request, failed_refs)
        if not refreshed.ready:
            raise ValueError("failed-subset retry did not pass a fresh preflight")
        factory = request.failed_subset_factory
        if factory is None:
            raise ValueError("failed-subset retry factory is unavailable")
        new_request = factory(failed_refs)
        if new_request.request_digest != refreshed.request_digest:
            raise ValueError("retry request does not match refreshed preflight")
        new_ref = self.submit(new_request, owner)
        if new_ref.run_id == previous.run_id:
            raise RuntimeError("retry must create a distinct Run ID")
        return new_ref

    def _trim(self) -> None:
        while len(self._results) > self._max_results:
            run_id = self._order.pop(0)
            if run_id not in self._results:
                continue
            self._results.pop(run_id, None)
            self._requests.pop(run_id, None)
            self._owners.pop(run_id, None)


def _object_results(result: OperationResult, refs: tuple[str, ...]) -> tuple[OperationObjectResult, ...]:
    value = result.value
    if isinstance(value, dict) and isinstance(value.get("outcomes"), (list, tuple)):
        output = []
        for item in value["outcomes"]:
            if not isinstance(item, dict):
                continue
            raw_status = str(item.get("status", "failed"))
            try:
                status = OperationObjectStatus(raw_status)
            except ValueError:
                status = OperationObjectStatus.FAILED
            output.append(
                OperationObjectResult(
                    str(
                        item.get("object_ref")
                        or item.get("item_id")
                        or item.get("entry_key")
                        or item.get("id")
                        or "unknown"
                    ),
                    str(
                        item.get("label")
                        or item.get("object_ref")
                        or item.get("item_id")
                        or item.get("entry_key")
                        or "对象"
                    ),
                    status,
                    str(item.get("code", "")),
                    bool(item.get("retryable", False)),
                )
            )
        if output:
            return tuple(output)
    if refs:
        status = {
            OperationOutcome.COMPLETED: OperationObjectStatus.SUCCEEDED,
            OperationOutcome.PARTIAL: OperationObjectStatus.FAILED,
            OperationOutcome.FAILED: OperationObjectStatus.FAILED,
            OperationOutcome.CANCELLED: OperationObjectStatus.CANCELLED,
        }[result.outcome]
        return tuple(OperationObjectResult(item, item, status) for item in refs)
    return ()
