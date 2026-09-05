"""Non-blocking UI binding for safe ReAct tool calls."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import logging

from PyQt6.QtCore import QObject, Qt, pyqtSignal

logger = logging.getLogger(__name__)


class _MainThreadBridge(QObject):
    requested = pyqtSignal(object)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.requested.connect(lambda callback: callback(), Qt.ConnectionType.QueuedConnection)


class ReactExecutionBinding:
    """Run retry-eligible reads on one worker and finalize them on the GUI thread."""

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
        self._closed = False

    def execute(self, steps: list[dict]) -> bool:
        if self._closed or not self._handler.can_execute_detached(steps):
            return False
        generation = self._generation
        future = self._executor.submit(
            self._handler.execute_steps_detached,
            deepcopy(steps),
            lambda message, g=generation: self._queue_status(g, message),
            lambda g=generation: self._closed or g != self._generation,
        )
        future.add_done_callback(lambda completed, g=generation: self._dispatch_result(g, completed))
        return True

    def abort(self) -> None:
        self._generation += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.abort()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _dispatch_result(self, generation: int, future: Future) -> None:
        try:
            completed = future.result()
        except Exception as exc:  # noqa: BLE001 - preserve UI loop on worker failure
            from transbridge.application.security.redaction import SecretRedactor

            safe_error = SecretRedactor.default().redact_text(str(exc))
            logger.error("ReAct tool worker failed (%s): %s", type(exc).__name__, safe_error)
            self._bridge.requested.emit(lambda g=generation: self._finish_failed(g))
            return
        self._bridge.requested.emit(lambda g=generation, items=completed: self._finish(g, items))

    def _queue_status(self, generation: int, message: str) -> None:
        self._bridge.requested.emit(lambda g=generation, text=message: self._show_status(g, text))

    def _show_status(self, generation: int, message: str) -> None:
        if not self._closed and generation == self._generation:
            self._system_message(message)

    def _finish(self, generation: int, completed) -> None:
        if self._closed or generation != self._generation:
            return
        self._handler.complete_detached(completed)
        controller = self._controller()
        if getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])

    def _finish_failed(self, generation: int) -> None:
        if self._closed or generation != self._generation:
            return
        self._system_message("工具执行线程异常，已继续对话。")
        controller = self._controller()
        if getattr(getattr(controller, "state", None), "value", "") == "executing":
            controller.handle_execution_complete([])
