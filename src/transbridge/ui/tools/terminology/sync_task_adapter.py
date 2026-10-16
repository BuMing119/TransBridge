"""TaskRuntime subscription for the terminology sync presenter."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobSnapshot, OwnerRef, TaskRuntime
from transbridge.application.tasks.activity import TaskActivityViewState
from transbridge.ui.presentation.task_projection import TaskProjectionBinding


class TerminologySyncTaskAdapter:
    def __init__(
        self,
        runtime: TaskRuntime,
        actor: OwnerRef,
        on_change: Callable[[TaskActivityViewState], None],
    ) -> None:
        self._runtime = runtime
        self._actor = actor
        self._on_change = on_change
        self._lock = RLock()
        self._seeding = False
        self._seed = None
        self._binding = TaskProjectionBinding(runtime, actor, self._project)

    @property
    def closed(self) -> bool:
        return self._binding.closed

    def start(self) -> None:
        with self._lock:
            self._seeding = True
            self._seed = None
        try:
            self._binding.start()
        finally:
            with self._lock:
                self._seeding = False
        with self._lock:
            if self._seed is not None:
                self._on_change(self._seed[1])
                self._seed = None

    def close(self) -> None:
        self._binding.close()

    def cancel(self, ref: JobRef) -> JobSnapshot:
        return self._runtime.cancel(ref, self._actor)

    def _project(self, activity) -> None:
        if not activity.job_type.startswith("operation.terminology_sync."):
            return
        with self._lock:
            if self._seeding:
                ref = JobRef(activity.job_id, activity.owner.owner_id, activity.run_id)
                snapshot = self._runtime.get(ref, self._actor)
                if self._seed is None or snapshot.created_at >= self._seed[0]:
                    self._seed = (snapshot.created_at, activity)
                return
            self._on_change(activity)


__all__ = ["TerminologySyncTaskAdapter"]
