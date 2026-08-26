"""Truthful task projections for AI QThread workloads during migration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import threading

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import (
    JobCapabilities,
    JobSnapshot,
    JobSpec,
    JobState,
    OwnerRef,
    TaskRuntime,
    TransitionError,
    activity_from_snapshot,
)
from transbridge.application.tasks.events import JobEvent, Subscription, TaskEventFilter

from .run_spec import AiRunSpec


class AiLegacyRunState(StrEnum):
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AiLegacyActivity:
    spec: AiRunSpec
    state: AiLegacyRunState
    revision: int = 0
    current: int = 0
    total: int = 0
    message: str = "准备中"
    diagnostic: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            AiLegacyRunState.CANCELLED,
            AiLegacyRunState.COMPLETED,
            AiLegacyRunState.FAILED,
        }


class LegacyAiTaskAdapter:
    def __init__(self, spec: AiRunSpec) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._created_at = datetime.now().astimezone()
        self._activity = AiLegacyActivity(spec, AiLegacyRunState.RUNNING)

    @property
    def activity(self) -> AiLegacyActivity:
        with self._lock:
            return self._activity

    @property
    def task_activity(self):
        with self._lock:
            value = self._activity
            snapshot = JobSnapshot(
                ref=JobRef(value.spec.run_id, value.spec.owner.owner_id, value.spec.run_id),
                owner=value.spec.owner,
                specification=_job_spec(value.spec, expose_controls=True),
                state=_job_state(value.state),
                revision=value.revision,
                sequence=value.revision + 1,
                created_at=self._created_at,
                updated_at=datetime.now().astimezone(),
                progress=(("current", value.current), ("total", value.total), ("message", value.message)),
            )
            return activity_from_snapshot(snapshot)

    def progress(self, current: int, total: int, message: str) -> bool:
        with self._lock:
            if self._closed or self._activity.is_terminal:
                return False
            self._activity = replace(
                self._activity,
                revision=self._activity.revision + 1,
                current=max(0, current),
                total=max(0, total),
                message=message,
            )
            return True

    def request_cancel(self) -> bool:
        with self._lock:
            if self._closed or self._activity.is_terminal:
                return False
            self._activity = replace(
                self._activity,
                state=AiLegacyRunState.CANCELLING,
                revision=self._activity.revision + 1,
                message="正在等待安全停止点",
            )
            return True

    def pause(self) -> bool:
        return self._control_message("已暂停")

    def resume(self) -> bool:
        return self._control_message("执行中")

    def finish(self, *, cancelled: bool = False) -> bool:
        return self._terminal(AiLegacyRunState.CANCELLED if cancelled else AiLegacyRunState.COMPLETED, None)

    def fail(self, diagnostic: str) -> bool:
        return self._terminal(AiLegacyRunState.FAILED, diagnostic)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def bind_worker(self, _worker: object) -> None:
        """Legacy controls are already wired directly by their progress window."""

    def _control_message(self, message: str) -> bool:
        with self._lock:
            if self._closed or self._activity.is_terminal:
                return False
            self._activity = replace(
                self._activity,
                revision=self._activity.revision + 1,
                message=message,
            )
            return True

    def _terminal(self, state: AiLegacyRunState, diagnostic: str | None) -> bool:
        with self._lock:
            if self._closed or self._activity.is_terminal:
                return False
            if state is AiLegacyRunState.CANCELLED and self._activity.state is not AiLegacyRunState.CANCELLING:
                return False
            self._activity = replace(
                self._activity,
                state=state,
                revision=self._activity.revision + 1,
                diagnostic=diagnostic,
            )
            return True


class TaskRuntimeAiTaskAdapter:
    """Mirror confirmed external-worker events into the real TaskRuntime."""

    def __init__(self, runtime: TaskRuntime, ref: JobRef, owner: OwnerRef, spec: AiRunSpec) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner
        self._spec = spec
        self._closed = False
        self._worker: object | None = None
        self._lock = threading.RLock()
        self._subscription: Subscription | None = runtime.subscribe(
            self._on_runtime_event,
            event_filter=TaskEventFilter(run_id=ref.run_id),
        )

    @property
    def task_activity(self):
        return activity_from_snapshot(self._runtime.get(self._ref, self._owner))

    @property
    def activity(self) -> AiLegacyActivity:
        snapshot = self._runtime.get(self._ref, self._owner)
        progress = dict(snapshot.progress)
        return AiLegacyActivity(
            spec=self._spec,
            state=_legacy_state(snapshot.state),
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

        with self._lock:
            self._worker = worker
            state = self._runtime.get(self._ref, self._owner).state
        self._forward_state(state)

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
            self._worker = None
        self._release_subscription()

    def _release_subscription(self) -> None:
        with self._lock:
            subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()

    def _on_runtime_event(self, event: JobEvent) -> None:
        self._forward_state(event.snapshot.state)

    def _forward_state(self, state: JobState) -> None:
        with self._lock:
            if self._closed or self._worker is None:
                return
            worker = self._worker
        action = {
            JobState.PAUSED: "pause",
            JobState.RUNNING: "resume",
            JobState.CANCELLING: "stop",
            JobState.CANCELLED: "stop",
        }.get(state)
        if action is None:
            return
        callback = getattr(worker, action, None)
        if not callable(callback) and action == "stop":
            callback = getattr(worker, "cancel", None)
        if callable(callback):
            callback()


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
        ),
    )


def runtime_job_spec(spec: AiRunSpec) -> JobSpec:
    return _job_spec(spec, expose_controls=True)


def _job_state(state: AiLegacyRunState) -> JobState:
    return {
        AiLegacyRunState.RUNNING: JobState.RUNNING,
        AiLegacyRunState.CANCELLING: JobState.CANCELLING,
        AiLegacyRunState.CANCELLED: JobState.CANCELLED,
        AiLegacyRunState.COMPLETED: JobState.COMPLETED,
        AiLegacyRunState.FAILED: JobState.FAILED,
    }[state]


def _legacy_state(state: JobState) -> AiLegacyRunState:
    return {
        JobState.QUEUED: AiLegacyRunState.RUNNING,
        JobState.RUNNING: AiLegacyRunState.RUNNING,
        JobState.PAUSED: AiLegacyRunState.RUNNING,
        JobState.CANCELLING: AiLegacyRunState.CANCELLING,
        JobState.CANCELLED: AiLegacyRunState.CANCELLED,
        JobState.COMPLETED: AiLegacyRunState.COMPLETED,
        JobState.FAILED: AiLegacyRunState.FAILED,
    }[state]


__all__ = [
    "AiLegacyActivity",
    "AiLegacyRunState",
    "LegacyAiTaskAdapter",
    "TaskRuntimeAiTaskAdapter",
    "runtime_job_spec",
]
