"""Qt-free terminology task projection for tool views."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobState, OwnerRef, TaskRuntime, TransitionError
from transbridge.application.tasks.activity import TaskActivityViewState
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.presentation.task_projection import TaskProjectionBinding


@dataclass(frozen=True, slots=True)
class TerminologyTaskViewState:
    run_id: str
    workload_type: TerminologyWorkloadType
    state: JobState
    phase: str
    completed: int
    total: int
    current_object: str
    message: str
    revision: int
    sequence: int

    @property
    def is_terminal(self) -> bool:
        return self.state in {JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED}


class TerminologyTaskAdapter:
    """Project+Variant-scoped view over the shared task projection binding."""

    def __init__(
        self,
        runtime: TaskRuntime,
        actor: OwnerRef,
        on_change: Callable[[TerminologyTaskViewState], None],
    ) -> None:
        if actor.project_id is None or actor.variant_id is None:
            raise ValueError("terminology task projection requires Project and Variant scope")
        self._runtime = runtime
        self._actor = actor
        self._on_change = on_change
        self._lock = threading.RLock()
        self._states: dict[str, TerminologyTaskViewState] = {}
        self._binding = TaskProjectionBinding(runtime, actor, self._project)

    @property
    def closed(self) -> bool:
        return self._binding.closed

    def start(self) -> None:
        self._binding.start()

    def close(self) -> None:
        self._binding.close()

    def states(self) -> tuple[TerminologyTaskViewState, ...]:
        with self._lock:
            return tuple(sorted(self._states.values(), key=lambda state: (state.sequence, state.run_id)))

    def cancel(self, ref: JobRef) -> bool:
        try:
            self._runtime.cancel(ref, self._actor)
        except TransitionError:
            return False
        return True

    def _project(self, activity: TaskActivityViewState) -> None:
        try:
            workload_type = TerminologyWorkloadType(activity.job_type)
        except ValueError:
            return
        progress = dict(activity.progress)
        state = TerminologyTaskViewState(
            run_id=activity.run_id,
            workload_type=workload_type,
            state=activity.state,
            phase=str(progress.get("phase", "")),
            completed=_counter(progress, "completed"),
            total=_counter(progress, "total"),
            current_object=str(progress.get("current_object", "")),
            message=_state_message(activity.state),
            revision=activity.revision,
            sequence=activity.sequence,
        )
        with self._lock:
            if self._binding.closed:
                return
            self._states[activity.run_id] = state
        self._on_change(state)


def _counter(progress: dict[str, object], key: str) -> int:
    value = progress.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _state_message(state: JobState) -> str:
    return {
        JobState.QUEUED: "等待执行",
        JobState.RUNNING: "执行中",
        JobState.PAUSED: "已暂停",
        JobState.CANCELLING: "正在停止",
        JobState.CANCELLED: "已停止",
        JobState.COMPLETED: "已完成",
        JobState.FAILED: "执行失败",
    }[state]


__all__ = ["TerminologyTaskAdapter", "TerminologyTaskViewState"]
