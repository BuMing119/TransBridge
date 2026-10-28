"""Project real TaskRuntime jobs onto AI worker controls and progress."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import threading

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    JobState,
    OwnerRef,
    TaskRuntime,
    TransitionError,
    activity_from_snapshot,
)
from transbridge.application.tasks.events import JobEvent, Subscription, TaskEventFilter

from .run_spec import AiRunSpec
from .worker_controls import SerialAiWorkerControls


class AiRunState(StrEnum):
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AiActivity:
    spec: AiRunSpec
    state: AiRunState
    revision: int = 0
    current: int = 0
    total: int = 0
    message: str = "准备中"
    diagnostic: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            AiRunState.CANCELLED,
            AiRunState.COMPLETED,
            AiRunState.FAILED,
        }


class TaskRuntimeAiTaskAdapter:
    """Mirror confirmed external-worker events into the real TaskRuntime."""

    def __init__(self, runtime: TaskRuntime, ref: JobRef, owner: OwnerRef, spec: AiRunSpec) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner
        self._spec = spec
        self._closed = False
        self._controls = SerialAiWorkerControls()
        self._lock = threading.RLock()
        self._subscription: Subscription | None = runtime.subscribe(
            self._on_runtime_event,
            event_filter=TaskEventFilter(run_id=ref.run_id),
        )

    @property
    def task_activity(self):
        return activity_from_snapshot(self._runtime.get(self._ref, self._owner))

    @property
    def activity(self) -> AiActivity:
        snapshot = self._runtime.get(self._ref, self._owner)
        progress = dict(snapshot.progress)
        return AiActivity(
            spec=self._spec,
            state=_activity_state(snapshot.state),
            revision=snapshot.revision,
            current=int(progress.get("current", 0)),
            total=int(progress.get("total", 0)),
            message=str(progress.get("message", "准备中")),
        )

    def progress(self, current: int, total: int, message: str) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            self._runtime.update_progress(
                self._ref,
                self._owner,
                {"current": max(0, current), "total": max(0, total), "message": message},
            )
        except TransitionError:
            return False
        return True

    def request_cancel(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            self._runtime.cancel(self._ref, self._owner)
        except TransitionError:
            return False
        return True

    def pause(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            self._runtime.pause(self._ref, self._owner)
        except TransitionError:
            return False
        return True

    def resume(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            self._runtime.resume(self._ref, self._owner)
        except TransitionError:
            return False
        return True

    def bind_worker(self, worker: object) -> None:
        """Make every advertised TaskRuntime control reach the real QThread worker."""

        snapshot = self._runtime.get(self._ref, self._owner)
        self._controls.bind_worker(worker, snapshot)

    def unbind_worker(self, worker: object) -> None:
        self._controls.unbind_worker(worker)

    def bind_run(self, cancel_callback: Callable[[], None] | None) -> None:
        """Callback must enqueue cancellation onto the run's owning thread."""
        snapshot = self._runtime.get(self._ref, self._owner)
        self._controls.bind_run(cancel_callback, snapshot)

    @property
    def is_cancelled(self) -> bool:
        return self._runtime.get(self._ref, self._owner).state in {JobState.CANCELLING, JobState.CANCELLED}

    def require_active(self) -> None:
        state = self._runtime.get(self._ref, self._owner).state
        if self._closed or state is not JobState.RUNNING:
            raise RuntimeError(f"AI 任务当前状态为 {state.value}，不能应用结果或开始新的处理。")

    def commit_mutation(self, mutation: Callable[[], None]) -> None:
        """Authorize only the final in-memory mutation, never preparation or notifications."""
        if self._closed:
            raise RuntimeError("AI 任务已关闭，不能提交结果。")
        permit = self._runtime.commit_permit(self._ref, self._owner)
        result = self._runtime.try_commit(permit, mutation)
        if not result.accepted:
            raise RuntimeError(f"AI 任务结果未提交：{result.reason}")

    def finish(self, *, cancelled: bool = False) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            snapshot = self._runtime.get(self._ref, self._owner)
            if cancelled or snapshot.state is JobState.CANCELLING:
                self._runtime.finish_cancelled(self._ref, self._owner)
            else:
                self._runtime.complete(self._ref, self._owner)
        except TransitionError:
            return False
        self._release_subscription()
        return True

    def fail(self, _diagnostic: str) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            snapshot = self._runtime.get(self._ref, self._owner)
            if snapshot.state is JobState.CANCELLING:
                self._runtime.finish_cancelled(self._ref, self._owner)
            else:
                self._runtime.fail(self._ref, self._owner)
        except TransitionError:
            return False
        self._release_subscription()
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._controls.close()
        self._release_subscription()

    def _release_subscription(self) -> None:
        with self._lock:
            subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()

    def _on_runtime_event(self, event: JobEvent) -> None:
        self._controls.accept(event.snapshot)
        if event.snapshot.is_terminal:
            self._release_subscription()


def _job_spec(spec: AiRunSpec, *, expose_controls: bool) -> JobSpec:
    capabilities = spec.capabilities.task_controls if expose_controls else JobCapabilities(supports_cancel=False)
    return JobSpec(
        job_type=f"ai-{spec.mode}",
        input_ref=spec.input_ref,
        input_fingerprint=spec.input_fingerprint,
        display_name=f"AI {spec.mode}",
        config_digest=spec.config_digest,
        capabilities=capabilities,
        metadata=(
            ("generation", str(spec.generation)),
            ("migration", "external-qthread-adapter"),
            ("workflow", spec.execution_profile.summary),
            ("workflow_digest", spec.execution_profile.digest),
        )
        + (() if spec.terminology_snapshot is None else spec.terminology_snapshot.metadata()),
    )


def runtime_job_spec(spec: AiRunSpec) -> JobSpec:
    return _job_spec(spec, expose_controls=True)


def _activity_state(state: JobState) -> AiRunState:
    return {
        JobState.QUEUED: AiRunState.RUNNING,
        JobState.RUNNING: AiRunState.RUNNING,
        JobState.PAUSED: AiRunState.RUNNING,
        JobState.CANCELLING: AiRunState.CANCELLING,
        JobState.CANCELLED: AiRunState.CANCELLED,
        JobState.COMPLETED: AiRunState.COMPLETED,
        JobState.FAILED: AiRunState.FAILED,
    }[state]


__all__ = [
    "AiActivity",
    "AiRunState",
    "TaskRuntimeAiTaskAdapter",
    "runtime_job_spec",
]
