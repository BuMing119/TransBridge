"""Own plan engines and their runtime identities independently of presentation."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .confirmation_view import ConfirmationView

logger = logging.getLogger(__name__)


class PlanExecutionBinding:
    """Owns the legacy plan engine while SessionController remains authoritative."""

    def __init__(
        self,
        *,
        context,
        controller: Callable[[], object],
        middlewares: Callable[[], list],
        observability,
        conversation,
        hide_thinking: Callable[[], None],
        system_message: Callable[[str], None],
        retry_handler: Callable[[], object] | None = None,
        execution_context: Callable[[], object] | None = None,
        on_task_started: Callable[[str, str], None] | None = None,
        on_lifecycle_changed: Callable[[], None] | None = None,
    ) -> None:
        self._context = context
        self._execution_context = execution_context or (lambda: self._context)
        self._on_task_started = on_task_started or (
            lambda task_id, _run_id: self._system_message(f"计划任务已启动: {task_id}")
        )
        self._on_lifecycle_changed = on_lifecycle_changed or (lambda: None)
        self._controller = controller
        self._middlewares = middlewares
        self._observability = observability
        self._conversation = conversation
        self._hide_thinking = hide_thinking
        self._system_message = system_message
        self._retry_handler = retry_handler or (lambda: None)
        self._engine = None
        self._retired_engines: set = set()
        self._runtimes: dict = {}
        self._closed = False
        self._generation = 0

    @property
    def engine(self):
        return self._engine

    def confirm(self, steps: list, confirmation_view: ConfirmationView) -> None:
        if self._closed:
            return
        from transbridge.smart_assistant.execution_engine import ExecutionEngine
        from transbridge.smart_assistant.plan_task_runtime import PlanTaskRuntime
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        self._controller().handle_user_confirmed(steps, "plan")
        self._hide_thinking()
        self._dispose_engine()
        runtime = PlanTaskRuntime(self._execution_context())
        try:
            engine = ExecutionEngine(
                ToolRegistry,
                runtime.execution_context,
                middlewares=self._middlewares(),
                retry_handler=self._retry_handler(),
            )
        except Exception:
            runtime.close()
            raise
        self._engine = engine
        self._runtimes[engine] = runtime
        generation = self._generation
        engine.on_step_requires_confirmation(
            lambda node_id, prompt, choices, e=engine, g=generation: self._request_engine_decision(
                e, g, confirmation_view, node_id, prompt, choices
            )
        )
        self._observability.start_conversation(f"conv_{id(steps)}")
        for register, callback in (
            (engine.on_step_started, self._observability.on_step_started),
            (engine.on_step_finished, self._observability.on_step_finished),
            (engine.on_step_retrying, self._observability.on_step_retrying),
        ):
            register(
                lambda *args, e=engine, g=generation, cb=callback: (
                    cb(*args) if not self._closed and self._engine is e and self._generation == g else None
                )
            )
        self._on_task_started(runtime.task_id, runtime.run_id)
        self._on_lifecycle_changed()
        runtime.start(
            engine,
            steps,
            finished=lambda results, e=engine, g=generation: confirmation_view.dispatch(
                lambda: self._on_all_finished(e, g, results)
            ),
            failed=lambda error, e=engine, g=generation: confirmation_view.dispatch(
                lambda: self._on_execution_failed(e, g, error)
            ),
        )

    def _request_engine_decision(self, engine, generation, view, node_id, prompt, choices) -> None:
        view.request_engine_decision(
            node_id,
            prompt,
            choices,
            engine=engine,
            is_current=lambda: not self._closed and self._engine is engine and self._generation == generation,
        )
        if engine in self._retired_engines:
            # A detached plan cannot retain confirmation authority or wait for a
            # dialog belonging to a superseded conversation round.
            engine.provide_decision(node_id, "终止")
            runtime = self._runtimes.get(engine)
            if runtime is not None:
                runtime.cancel()

    def cancel(self) -> None:
        if self._closed:
            return
        self._dispose_engine()
        self._hide_thinking()
        self._system_message("计划已取消")
        controller = self._controller()
        if getattr(getattr(controller, "state", None), "value", "") == "awaiting":
            controller.handle_user_cancelled()
        else:
            controller.handle_abort()
        self._on_lifecycle_changed()

    def abort(self) -> None:
        self._dispose_engine()

    def interrupt_round(self) -> None:
        """Detach plan presentation while its already admitted work completes."""
        self._generation += 1
        engine = self._engine
        self._engine = None
        if engine is not None:
            self._retired_engines.add(engine)
            self._observability.end_conversation()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dispose_engine()
        for engine in tuple(self._retired_engines):
            self._release_engine(engine)
        self._retired_engines.clear()

    def _on_all_finished(self, engine, generation: int, results: list) -> None:
        if engine in self._retired_engines:
            self._retired_engines.discard(engine)
            self._release_engine(engine)
            return
        if self._closed or self._engine is not engine or self._generation != generation:
            return
        cancelled = any(
            isinstance(result.data, dict) and result.data.get("status") == "cancelled" for result in results
        )
        lines = []
        for result in results:
            was_cancelled = isinstance(result.data, dict) and result.data.get("status") == "cancelled"
            icon = "[CANCELLED]" if was_cancelled else "[OK]" if result.success else "[FAIL]"
            lines.append(f"{icon} 步骤 {result.step_id} ({result.tool}): {result.message}")
        summary = "\n".join(lines)
        title = "计划已取消" if cancelled else "计划执行完成"
        self._system_message(f"【{title}】\n{summary}")
        structured_results = [
            {
                "step_id": result.step_id,
                "tool": result.tool,
                "success": result.success,
                "message": result.message,
                "status": (
                    "cancelled"
                    if isinstance(result.data, dict) and result.data.get("status") == "cancelled"
                    else "completed"
                    if result.success
                    else "failed"
                ),
            }
            for result in results
        ]
        self._conversation.add_plan_result(
            summary,
            success=all(result.success for result in results),
            results=structured_results,
        )
        self._observability.end_conversation()
        self._dispose_engine()
        controller = self._controller()
        if cancelled:
            controller.handle_round_interrupted()
        else:
            controller.handle_execution_complete(results)
        self._on_lifecycle_changed()

    def _on_execution_failed(self, engine, generation: int, error: Exception) -> None:
        if engine in self._retired_engines:
            self._retired_engines.discard(engine)
            self._release_engine(engine)
            logger.warning("Detached plan execution failed (%s)", type(error).__name__)
            return
        if self._closed or self._engine is not engine or self._generation != generation:
            return
        summary = f"计划执行失败: {type(error).__name__}: {str(error)[:300]}"
        self._system_message(summary)
        self._conversation.add_plan_result(summary, success=False)
        self._observability.end_conversation()
        self._dispose_engine()
        controller = self._controller()
        if getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])
        else:
            controller.handle_abort()
        self._on_lifecycle_changed()

    def _dispose_engine(self) -> None:
        self._generation += 1
        engine = self._engine
        self._engine = None
        if engine is not None:
            self._release_engine(engine)

    def _release_engine(self, engine) -> None:
        runtime = self._runtimes.pop(engine, None)
        if runtime is not None:
            runtime.close()
        engine.cancel()
        # Cancellation is cooperative; the GUI must not join in-flight I/O.
        engine._executor.shutdown(wait=False)
