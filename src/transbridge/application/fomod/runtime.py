"""TaskRuntime adapters for typed FOMOD workloads."""

from __future__ import annotations

from collections.abc import Callable
import threading

from transbridge.application.contracts import JobRef, OperationOutcome
from transbridge.application.tasks import (
    JobState,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
    TransitionError,
)

from .models import FomodRunSpec, PipelineResult
from .pipeline import PipelineEngine


class FomodPipelineFailed(RuntimeError):
    def __init__(self, report: PipelineResult) -> None:
        self.report = report
        codes = ",".join(diagnostic.code for diagnostic in report.diagnostics)
        super().__init__(f"FOMOD pipeline failed: {codes or 'unknown'}")


class TaskRuntimeRunGuard:
    def __init__(self, runtime: TaskRuntime, ref: JobRef, owner: OwnerRef) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner

    def allows(self, run_id: str) -> bool:
        if run_id != self._ref.run_id:
            raise ValueError("FOMOD RunSpec does not match the scheduled TaskRuntime run")
        snapshot = self._runtime.get(self._ref, self._owner)
        return snapshot.state is JobState.RUNNING


class TaskRuntimeCommitGuard:
    """Issue and consume the runtime's one-shot permit around publication."""

    def __init__(self, runtime: TaskRuntime, ref: JobRef, owner: OwnerRef) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner

    def commit(self, run_id: str, mutation: Callable[[], None]) -> bool:
        if run_id != self._ref.run_id:
            raise ValueError("FOMOD RunSpec does not match the scheduled TaskRuntime run")
        try:
            permit = self._runtime.commit_permit(self._ref, self._owner)
        except TransitionError:
            snapshot = self._runtime.get(self._ref, self._owner)
            if snapshot.state in {JobState.CANCELLING, JobState.CANCELLED}:
                return False
            raise
        result = self._runtime.try_commit(permit, mutation)
        if result.accepted:
            return True
        if result.reason in {"cancelled", "terminal_or_inactive"} and result.snapshot.state in {
            JobState.CANCELLING,
            JobState.CANCELLED,
        }:
            return False
        raise RuntimeError(f"FOMOD_COMMIT_REJECTED:{result.reason}")


class FomodPipelineWorkload:
    """Return reports to projections while leaving terminal state to TaskRuntime."""

    def __init__(
        self,
        spec: FomodRunSpec,
        engine: PipelineEngine,
        *,
        on_report: Callable[[PipelineResult], None] | None = None,
    ) -> None:
        self._spec = spec
        self._engine = engine
        self._on_report = on_report
        self._lock = threading.Lock()
        self._last_report: PipelineResult | None = None

    @property
    def last_report(self) -> PipelineResult | None:
        with self._lock:
            return self._last_report

    def __call__(self, cancellation) -> PipelineResult:
        report = self._engine.run(self._spec, cancellation)
        with self._lock:
            self._last_report = report
        if self._on_report is not None:
            try:
                self._on_report(report)
            except Exception:
                # Report consumers are projections and cannot own job terminal state.
                pass
        if report.outcome is OperationOutcome.CANCELLED:
            raise TaskCancelled("FOMOD pipeline cancelled")
        if report.outcome is OperationOutcome.FAILED:
            raise FomodPipelineFailed(report)
        return report
