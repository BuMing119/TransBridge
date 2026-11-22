# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Non-blocking UI binding for safe ReAct tool calls."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import logging
from threading import Event

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from transbridge.smart_assistant.tool_execution_handler import DETACHED_WRITE_TOOLS

logger = logging.getLogger(__name__)


class _MainThreadBridge(QObject):
    requested = pyqtSignal(object)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.requested.connect(lambda callback: callback(), Qt.ConnectionType.QueuedConnection)


class ReactExecutionBinding:
    """Run detached reads and confirmed writes, returning results to their owning cards."""

    def __init__(
        self,
        *,
        parent,
        handler,
        controller: Callable[[], object],
        system_message: Callable[[str], None],
    ) -> None:
        self._handler = handler
        self._controller = controller
        self._system_message = system_message
        self._bridge = _MainThreadBridge(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transbridge-react-tool")
        self._generation = 0
        self._cancel = Event()
        self._closed = False

    def execute(self, steps: list[dict]) -> bool:
        if self._closed:
            return False
        if self._handler.can_execute_detached(steps):
            self._submit_reads(steps, self._generation)
        elif any(step.get("tool") in DETACHED_WRITE_TOOLS for step in steps):
            self._run_confirmed_batch(tuple(steps), self._generation)
        else:
            return False
        return True

    def _run_confirmed_batch(self, steps, generation):
        for index, step in enumerate(steps):
            if self._closed or generation != self._generation:
                return
            remaining = steps[index + 1 :]
            if step.get("tool") in DETACHED_WRITE_TOOLS:
                run = self._handler.prepare_detached_write(step, self._cancel)
                if run is None:
                    continue
                if self._closed or generation != self._generation:
                    return  # The permission dialog may have yielded to a session change.
                future = self._executor.submit(run)
                sources = ((step, step),)
                future.add_done_callback(lambda done: self._dispatch_result(generation, done, sources, remaining))
                return
            if self._handler.can_execute_detached([step]):
                self._submit_reads([step], generation, remaining)
                return
            self._handler.execute_step(step, skip_react_continue=True)
        self._finish(generation, ())

    def _submit_reads(self, steps, generation, remaining=()):
        detached_steps = deepcopy(steps)
        sources = tuple(zip(detached_steps, steps, strict=True))
        future = self._executor.submit(
            self._handler.execute_steps_detached,
            detached_steps,
            lambda message, g=generation: self._queue_status(g, message),
            lambda g=generation: self._closed or g != self._generation,
        )
        future.add_done_callback(lambda completed: self._dispatch_result(generation, completed, sources, remaining))

    def abort(self) -> None:
        self._cancel.set()
        self._cancel = Event()
        self._generation += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.abort()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _dispatch_result(self, generation: int, future: Future, sources: tuple = (), remaining: tuple = ()) -> None:
        if self._closed or generation != self._generation:
            return  # The request gate has already received the worker's own result.
        originals = tuple(original for _source, original in sources)
        try:
            completed = future.result()
        except Exception as exc:  # noqa: BLE001 - preserve UI loop on worker failure
            from transbridge.application.security import diagnostic_exception_text

            safe_error = diagnostic_exception_text(exc)
            logger.error("ReAct tool worker failed (%s): %s", type(exc).__name__, safe_error)
            self._bridge.requested.emit(
                lambda g=generation, message=safe_error: self._finish_failed(g, originals + remaining, message)
            )
            return
        if sources:
            completed = tuple(
                replace(
                    item,
                    source_step=next((original for source, original in sources if item.source_step is source), None),
                )
                for item in completed
            )
        self._bridge.requested.emit(lambda g=generation, items=completed: self._finish(g, items, originals, remaining))

    def _queue_status(self, generation: int, message: str) -> None:
        self._bridge.requested.emit(lambda g=generation, text=message: self._show_status(g, text))

    def _show_status(self, generation: int, message: str) -> None:
        if not self._closed and generation == self._generation:
            self._system_message(message)

    def _finish(self, generation: int, completed, originals: tuple = (), remaining: tuple = ()) -> None:
        if self._closed or generation != self._generation:
            return
        self._handler.complete_detached(completed)
        missing = [step for step in originals if not any(item.source_step is step for item in completed)]
        if missing:
            self._handler.report_unavailable_results(missing, "执行结束，但未收到此操作的结果；请核对请求记录。")
        if remaining:
            self._run_confirmed_batch(remaining, generation)
            return
        controller = self._controller()
        if getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])

    def _finish_failed(self, generation: int, originals: tuple = (), error: str = "") -> None:
        if self._closed or generation != self._generation:
            return
        self._system_message("工具执行线程异常，已继续对话。")
        report = getattr(self._handler, "report_unavailable_results", None)
        if report is not None:
            report(list(originals), f"未收到操作结果，请核对请求记录。原因：{error or '工具执行线程异常'}")
        controller = self._controller()
        if getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])
