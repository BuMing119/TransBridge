"""TaskRuntime subscription for the terminology sync presenter."""

from __future__ import annotations

from collections.abc import Callable

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
        self._binding = TaskProjectionBinding(runtime, actor, self._project)

    @property
    def closed(self) -> bool:
        return self._binding.closed

    def start(self) -> None:
        self._binding.start()

    def close(self) -> None:
        self._binding.close()

    def cancel(self, ref: JobRef) -> JobSnapshot:
        return self._runtime.cancel(ref, self._actor)

    def _project(self, activity) -> None:
        if not activity.job_type.startswith("operation.terminology_sync."):
            return
        self._on_change(activity)


__all__ = ["TerminologySyncTaskAdapter"]
