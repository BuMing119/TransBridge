"""TaskRuntime integration for long-running terminology workloads.

The module owns orchestration only.  Builders, publishers, and renderers are
injected by composition so their repositories remain the authority for the
business-level expected-state comparison and mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import threading
import time
from typing import Protocol

from transbridge.application.contracts import Deferred, JobRef
from transbridge.application.tasks import (
    CancellationToken,
    JobState,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
)

from .workloads import (
    AnyTerminologyWorkloadRequest,
    TerminologyExpectedState,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
    terminology_job_spec,
)

DEFAULT_TERMINOLOGY_HEARTBEAT_SECONDS = 1.5


@dataclass(frozen=True, slots=True)
class TerminologyBusinessGuardResult:
    """Result of an atomic repository-side compare-and-mutate operation."""

    current: bool
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not self.current and (self.diagnostic is None or not self.diagnostic.strip()):
            raise ValueError("a rejected terminology business guard requires a diagnostic code")


class TerminologyCommitPort(Protocol):
    """Atomically compare every expected-state field and invoke ``mutation``.

    Implementations must compare Project and Variant revisions, source graph
    and source fingerprints, effective/base versions, draft identity/revision,
    and build freshness.  ``mutation`` must not be invoked when any comparison
    fails.
    """

    def commit_if_current(
        self,
        expected: TerminologyExpectedState,
        mutation: Callable[[], None],
    ) -> TerminologyBusinessGuardResult: ...


class UnavailableTerminologyCommitPort:
    """Fail-closed composition default until persistence wires a real guard."""

    def commit_if_current(
        self,
        expected: TerminologyExpectedState,
        mutation: Callable[[], None],
    ) -> TerminologyBusinessGuardResult:
        del expected, mutation
        return TerminologyBusinessGuardResult(False, "TERMINOLOGY_COMMIT_PORT_UNAVAILABLE")


class TerminologyRunLease:
    """Run-bound lease that quarantines late or foreign workload activity."""

    def __init__(
        self,
        runtime: TaskRuntime,
        ref: JobRef,
        owner: OwnerRef,
        *,
        input_fingerprint: str,
        cancellation: CancellationToken,
    ) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner
        self._input_fingerprint = input_fingerprint
        self._cancellation = cancellation

    @property
    def ref(self) -> JobRef:
        return self._ref

    @property
    def owner(self) -> OwnerRef:
        return self._owner

    def is_active(self) -> bool:
        if self._cancellation.is_cancelled:
            return False
        snapshot = self._runtime.get(self._ref, self._owner)
        return (
            snapshot.ref.run_id == self._ref.run_id
            and snapshot.state is JobState.RUNNING
            and snapshot.specification.input_fingerprint == self._input_fingerprint
        )

    def can_refill(self) -> bool:
        """Whether a fan-out workload may submit another unit of work."""

        return self.is_active()

    def raise_if_inactive(self) -> None:
        if self._cancellation.is_cancelled:
            self._cancellation.raise_if_cancelled()
        if not self.is_active():
            raise RuntimeError("terminology run lease is stale or inactive")


class ProgressHeartbeat:
    """Emit stable progress and cooperative heartbeats without another thread."""

    def __init__(
        self,
        runtime: TaskRuntime,
        lease: TerminologyRunLease,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        interval_seconds: float = 2.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._runtime = runtime
        self._lease = lease
        self._clock = monotonic_clock
        self._interval = interval_seconds
        self._lock = threading.Lock()
        self._latest: TerminologyProgress | None = None
        self._latest_signature: tuple[object, ...] | None = None
        self._last_emitted_at: float | None = None
        self._heartbeat_sequence = 0

    @property
    def latest(self) -> TerminologyProgress | None:
        with self._lock:
            return self._latest

    def update(self, progress: TerminologyProgress, *, force: bool = False) -> None:
        signature = (progress.phase, progress.current_object, *progress.count_signature)
        with self._lock:
            changed = signature != self._latest_signature
            self._latest = progress
            if not (force or changed):
                return
            self._emit_locked(progress)

    def pulse(self) -> bool:
        """Emit the unchanged snapshot once two seconds have elapsed."""

        with self._lock:
            if self._latest is None:
                return False
            now = self._clock()
            if self._last_emitted_at is not None and now - self._last_emitted_at < self._interval:
                return False
            self._heartbeat_sequence += 1
            self._emit_locked(self._latest, emitted_at=now)
            return True

    def _emit_locked(self, progress: TerminologyProgress, *, emitted_at: float | None = None) -> None:
        self._lease.raise_if_inactive()
        self._runtime.update_progress(
            self._lease.ref,
            self._lease.owner,
            progress.to_payload(heartbeat_sequence=self._heartbeat_sequence),
        )
        self._latest_signature = (progress.phase, progress.current_object, *progress.count_signature)
        self._last_emitted_at = self._clock() if emitted_at is None else emitted_at


@dataclass(frozen=True, slots=True)
class TerminologyExecutionContext:
    cancellation: CancellationToken
    lease: TerminologyRunLease
    progress: ProgressHeartbeat

    def checkpoint(self) -> None:
        self.cancellation.raise_if_cancelled()
        self.lease.raise_if_inactive()

    def can_refill(self) -> bool:
        return self.lease.can_refill()

    def heartbeat(self) -> bool:
        self.checkpoint()
        return self.progress.pulse()


@dataclass(frozen=True, slots=True)
class TerminologyWorkloadExecution:
    """Staged result; ``mutation`` is the only business publication point."""

    result: TerminologyWorkloadResult
    mutation: Callable[[], None]


class TerminologyWorkloadRunner(Protocol):
    def __call__(
        self,
        request: AnyTerminologyWorkloadRequest,
        context: TerminologyExecutionContext,
    ) -> TerminologyWorkloadExecution: ...


class TerminologyWorkloadUnavailableError(LookupError):
    """Raised before submission when composition did not bind a workload."""

    code = "TERMINOLOGY_WORKLOAD_UNAVAILABLE"

    def __init__(self, workload_type: TerminologyWorkloadType) -> None:
        self.workload_type = workload_type
        super().__init__(f"{self.code}: no runner is bound for {workload_type.value}")


class TerminologyWorkloadRegistry:
    """Central catalog for all four workload types and their injected runners."""

    def __init__(self) -> None:
        self._runners: dict[TerminologyWorkloadType, TerminologyWorkloadRunner] = {}
        self._lock = threading.RLock()

    @property
    def workload_types(self) -> tuple[TerminologyWorkloadType, ...]:
        with self._lock:
            return tuple(workload_type for workload_type in TerminologyWorkloadType if workload_type in self._runners)

    def bind(self, workload_type: TerminologyWorkloadType, runner: TerminologyWorkloadRunner) -> None:
        with self._lock:
            self._runners[workload_type] = runner

    def runner_for(self, workload_type: TerminologyWorkloadType) -> TerminologyWorkloadRunner:
        with self._lock:
            try:
                return self._runners[workload_type]
            except KeyError as exc:
                raise TerminologyWorkloadUnavailableError(workload_type) from exc


@dataclass(frozen=True, slots=True)
class TerminologyCommitOutcome:
    runtime_accepted: bool
    business_current: bool
    committed: bool
    diagnostic: str | None = None


class TerminologyCommitGuard:
    """Combine the TaskRuntime permit and repository expected-state guard."""

    def __init__(
        self,
        runtime: TaskRuntime,
        lease: TerminologyRunLease,
        commit_port: TerminologyCommitPort,
    ) -> None:
        self._runtime = runtime
        self._lease = lease
        self._commit_port = commit_port

    def commit(
        self,
        expected: TerminologyExpectedState,
        mutation: Callable[[], None],
    ) -> TerminologyCommitOutcome:
        self._lease.raise_if_inactive()
        permit = self._runtime.commit_permit(self._lease.ref, self._lease.owner)
        business_result: TerminologyBusinessGuardResult | None = None

        def guarded_mutation() -> None:
            nonlocal business_result
            business_result = self._commit_port.commit_if_current(expected, mutation)

        accepted = self._runtime.try_commit(permit, guarded_mutation)
        if not accepted.accepted:
            if accepted.reason == "cancelled":
                raise TaskCancelled("cancelled before terminology commit")
            raise RuntimeError(f"terminology runtime commit rejected: {accepted.reason}")
        if business_result is None:
            raise RuntimeError("terminology commit port returned no guard result")
        return TerminologyCommitOutcome(
            runtime_accepted=True,
            business_current=business_result.current,
            committed=business_result.current,
            diagnostic=business_result.diagnostic,
        )


class TerminologyTaskEntrypoint:
    """Submit and observe terminology workloads through the shared TaskRuntime."""

    def __init__(
        self,
        runtime: TaskRuntime,
        registry: TerminologyWorkloadRegistry,
        commit_port: TerminologyCommitPort,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        heartbeat_seconds: float = DEFAULT_TERMINOLOGY_HEARTBEAT_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._commit_port = commit_port
        self._monotonic_clock = monotonic_clock
        self._heartbeat_seconds = heartbeat_seconds
        self._lock = threading.RLock()
        self._requests: dict[str, AnyTerminologyWorkloadRequest] = {}
        self._results: dict[str, TerminologyWorkloadResult] = {}

    @property
    def runtime(self) -> TaskRuntime:
        return self._runtime

    def submit(
        self,
        request: AnyTerminologyWorkloadRequest,
        owner: OwnerRef,
    ) -> Deferred[JobRef]:
        self._validate_owner(request, owner)
        runner = self._registry.runner_for(request.workload_type)
        specification = terminology_job_spec(request)
        deferred = self._runtime.submit(specification, owner)
        ref = deferred.ref
        run_id = ref.run_id or ref.job_id
        with self._lock:
            self._requests[run_id] = request

        def workload(cancellation: CancellationToken) -> None:
            lease = TerminologyRunLease(
                self._runtime,
                ref,
                owner,
                input_fingerprint=specification.input_fingerprint,
                cancellation=cancellation,
            )
            heartbeat = ProgressHeartbeat(
                self._runtime,
                lease,
                monotonic_clock=self._monotonic_clock,
                interval_seconds=self._heartbeat_seconds,
            )
            context = TerminologyExecutionContext(cancellation, lease, heartbeat)
            heartbeat.update(TerminologyProgress(phase=_initial_phase(request.workload_type)))
            execution = runner(request, context)
            if execution.result.workload_type is not request.workload_type:
                raise ValueError("terminology runner returned a result for another workload type")
            context.checkpoint()

            latest = heartbeat.latest or TerminologyProgress(phase=TerminologyPhase.FINALIZE)
            heartbeat.update(replace(latest, phase=TerminologyPhase.FINALIZE), force=True)
            # No progress may be emitted after this point: progress invalidates
            # the formal permit by advancing the runtime revision.
            outcome = TerminologyCommitGuard(self._runtime, lease, self._commit_port).commit(
                request.expected,
                execution.mutation,
            )
            result = (
                execution.result.published()
                if outcome.committed
                else execution.result.stale(outcome.diagnostic or "TERMINOLOGY_EXPECTED_STATE_STALE")
            )
            with self._lock:
                self._results[run_id] = result

        self._runtime.schedule(ref, owner, workload)
        return deferred

    def request(self, ref: JobRef, actor: OwnerRef) -> AnyTerminologyWorkloadRequest:
        self._runtime.get(ref, actor)
        run_id = ref.run_id or ref.job_id
        with self._lock:
            return self._requests[run_id]

    def result(self, ref: JobRef, actor: OwnerRef) -> TerminologyWorkloadResult | None:
        self._runtime.get(ref, actor)
        run_id = ref.run_id or ref.job_id
        with self._lock:
            return self._results.get(run_id)

    def cancel(self, ref: JobRef, actor: OwnerRef) -> None:
        self._runtime.cancel(ref, actor)

    @staticmethod
    def _validate_owner(request: AnyTerminologyWorkloadRequest, owner: OwnerRef) -> None:
        if owner.project_id is None or owner.variant_id is None:
            raise ValueError("terminology workload owner requires Project and Variant scope")
        if (owner.project_id, owner.variant_id) != (request.project_id, request.variant_id):
            raise ValueError("terminology workload owner scope does not match the request")


def _initial_phase(workload_type: TerminologyWorkloadType) -> TerminologyPhase:
    if workload_type is TerminologyWorkloadType.BUILD:
        return TerminologyPhase.CAPTURE
    if workload_type is TerminologyWorkloadType.PUBLISH:
        return TerminologyPhase.VALIDATE
    return TerminologyPhase.RENDER


__all__ = [
    "DEFAULT_TERMINOLOGY_HEARTBEAT_SECONDS",
    "ProgressHeartbeat",
    "TerminologyBusinessGuardResult",
    "TerminologyCommitGuard",
    "TerminologyCommitOutcome",
    "TerminologyCommitPort",
    "TerminologyExecutionContext",
    "TerminologyRunLease",
    "TerminologyTaskEntrypoint",
    "TerminologyWorkloadExecution",
    "TerminologyWorkloadRegistry",
    "TerminologyWorkloadRunner",
    "TerminologyWorkloadUnavailableError",
    "UnavailableTerminologyCommitPort",
]
