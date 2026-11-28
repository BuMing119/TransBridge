# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Run the current-file dictionary commands from the desktop menu."""

import logging
from threading import Event

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from transbridge.application.tasks.controls import CancellationToken
from transbridge.smart_assistant.tools._dictionary_application import apply_dictionary, save_dictionary
from transbridge.smart_assistant.tools.types import ExecutionContext, ToolResult
from transbridge.ui.workers import ApiWorker

_logger = logging.getLogger(__name__)


class DictionaryCommands(QObject):
    busy_changed = pyqtSignal()

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._worker: ApiWorker | None = None
        self._cancel = Event()
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def save(self) -> None:
        self._start(save_dictionary, selected_only=False)

    def apply(self) -> None:
        self._start(apply_dictionary)

    def _start(self, operation, **kwargs) -> None:
        if self._closed or self.busy or self._host.context.collection is None:
            return
        try:
            # Bind the click to this source before yielding to the worker.
            execution = ExecutionContext(app_context=self._host.context)
            self._cancel = Event()
            execution.cancellation_token = CancellationToken(self._cancel)
            worker = ApiWorker(
                operation,
                execution,
                **kwargs,
                route_http_errors=False,
                report_api_status=False,
                cancel_read_on_close=self._cancel.set,
            )
            self._worker = worker
            worker.result.connect(self._result)
            worker.finished.connect(self._finished)
            worker.finished.connect(worker.deleteLater)
            worker.start()
        except Exception:
            _logger.exception("无法启动词典操作")
            if self._worker is not None:
                self._worker.deleteLater()
                self._worker = None
        self.busy_changed.emit()

    @pyqtSlot(object)
    def _result(self, result: ToolResult) -> None:
        if not result.success:
            _logger.warning("词典操作失败：%s", result.message)
            return
        for warning in result.warnings or ():
            _logger.warning("%s", warning)
        if self._closed:
            return
        if "saved" in result.data:
            saved = result.data["saved"]
            if saved:
                self._host.tool_windows.refresh_open_dictionary()
            message = f"已存入词典 {saved} 条" if saved else "没有新增或变更的词条"
        else:
            message = f"已填入 {result.data['applied']} 条译文"
        self._host.show_message(message)

    @pyqtSlot()
    def _finished(self) -> None:
        self._worker = None
        self.busy_changed.emit()

    def close(self) -> None:
        self._closed = True
        self._cancel.set()
