"""Register assistant plans as scoped, cancellable runtime workloads."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy
from dataclasses import replace

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import JobState, TaskCancelled, TaskEventFilter

from .execution_engine import StepResult
from .tools.base import ExecutionContext
from .tools.task_manager import TaskManager
from .tools.task_runtime_bridge import task_metadata


class _FailedPlanSteps(RuntimeError):
    """A completed graph contains failed steps; preserve them for presentation."""


class PlanTaskRuntime:
    """One parent job owns a plan engine and propagates its runtime controls."""

    def __init__(self, context, *, manager=None) -> None:
        self._manager = manager or TaskManager()
        metadata = task_metadata(context, {"job_type": "assistant-plan", "display_name": "助手计划"})
        metadata.pop("parent_task_id", None)
        # Parent dispatch is not a leaf write. Each tool creates its own effect intent.
        metadata.pop("assistant_effect_id", None)
        metadata.pop("assistant_execution_ref", None)
        self.task_id = self._manager.register(metadata=metadata)
        self._handle = self._manager.get_handle(self.task_id)
        self.run_id = self._handle.execution.ref.run_id
        captured = context if isinstance(context, ExecutionContext) else ExecutionContext(app_context=context)
        request = captured.request_context
        if isinstance(request, RequestContext):
            child_metadata = dict(request.metadata)
            child_metadata["parent_task_id"] = self.task_id
            request = replace(request, metadata=tuple(sorted(child_metadata.items())))
        self.execution_context = copy(captured)
        self.execution_context.request_context = request
        self.execution_context.assistant_effect_id = ""
        self._engine = None
        self._subscription = None
        self._started = False
        self._completed = False
        self._results: list[StepResult] = []
        self._error: Exception | None = None
        self._finished: Callable[[list], None] = lambda _results: None
        self._failed: Callable[[Exception], None] = lambda _error: None

    def start(
        self, engine, steps: list, *, finished: Callable[[list], None], failed: Callable[[Exception], None]
    ) -> None:
        if self._started:
            raise RuntimeError("plan workload was already started")
        self._started = True
        self._engine = engine
        self._finished = finished
        self._failed = failed
        self._subscription = self._manager.runtime.subscribe(
            self._observe, event_filter=TaskEventFilter(run_id=self.run_id)
        )
        self._reconcile()
        if self._completed:
            return

        def run() -> None:
            try:
                self._results = engine.execute(steps)
                if any(self._cancelled_step(result) for result in self._results):
                    self._manager.cancel(self.task_id)
                if self._handle.stop_event.is_set():
                    raise TaskCancelled("计划已取消")
                self._handle.result = {"completed_steps": sum(result.success for result in self._results)}
                if any(not result.success for result in self._results):
                    raise _FailedPlanSteps("计划包含失败步骤")
            except (TaskCancelled, _FailedPlanSteps):
                raise
            except Exception as error:
                self._error = error
                raise

        try:
            self._manager.start_thread(self.task_id, run)
        except Exception as error:
            self._error = error
            self._manager.set_status(self.task_id, "failed")
            self._manager.notify_finished(self.task_id, False, "计划线程无法启动")

    def cancel(self) -> None:
        self._manager.cancel(self.task_id)

    def close(self) -> None:
        """Cancel unfinished work; terminal delivery closes its subscription."""
        self.cancel()
        if not self._started:
            self._manager.set_status(self.task_id, "cancelled")
            self._manager.notify_finished(self.task_id, False, "计划已取消")

    @staticmethod
    def _cancelled_step(result: StepResult) -> bool:
        return isinstance(result.data, dict) and result.data.get("status") == "cancelled"

    def _observe(self, event) -> None:
        self._reconcile(event.previous_state)

    def _reconcile(self, previous_state=None) -> None:
        snapshot = self._manager.runtime.get(self._handle.execution.ref, self._handle.execution.owner)
        state = snapshot.state
        if state in {JobState.CANCELLING, JobState.CANCELLED}:
            self._engine.cancel()
        elif state is JobState.PAUSED:
            self._engine.pause()
        elif state is JobState.RUNNING and previous_state is JobState.PAUSED:
            self._engine.resume()
        if not snapshot.is_terminal or self._completed:
            return
        self._completed = True
        self._subscription.close()
        if state is JobState.FAILED and self._error is not None:
            self._failed(self._error)
            return
        results = list(self._results)
        if state is JobState.CANCELLED and not any(self._cancelled_step(result) for result in results):
            results.append(StepResult(0, "plan", False, "计划已取消", data={"status": "cancelled"}))
        self._finished(results)
