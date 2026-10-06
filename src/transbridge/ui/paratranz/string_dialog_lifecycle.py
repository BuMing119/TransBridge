"""Asynchronous close ownership for StringDetailDialog."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QProgressDialog

from transbridge.ui.worker_registry import get_api_worker_registry


class StringDialogLifecycle:
    def __init__(self, host, *, workers: Callable[[], Iterable[object]]) -> None:
        self._host = host
        self._workers = workers
        self._close_pending = False
        self._close_result: int | None = None
        self._close_progress: QProgressDialog | None = None
        self._close_timer = QTimer(host)
        self._close_timer.setInterval(25)
        self._close_timer.timeout.connect(self.finish_close_if_idle)

    def request_done(self, result: int) -> bool:
        """Gate every dialog exit while preserving the first requested result."""
        if self._close_pending:
            return False
        if not self._has_pending_workers():
            return True
        self._close_pending = True
        self._close_result = result
        self._host.setEnabled(False)
        progress = QProgressDialog("正在等待后台同步完成…", "", 0, 0, self._host)
        progress.setCancelButton(None)
        progress.setWindowTitle("正在关闭")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        self._close_progress = progress
        self._close_timer.start()
        return False

    def _has_pending_workers(self) -> bool:
        registry = get_api_worker_registry()
        return any(
            not sip.isdeleted(worker) and (registry.is_pending(worker) or worker.isRunning())
            for worker in self._workers()
        )

    def finish_close_if_idle(self) -> None:
        if not self._close_pending:
            return
        # Native completion can precede queued result delivery and a callback
        # can enqueue another sync worker. Poll through registry settlement.
        if self._has_pending_workers():
            return
        self._close_timer.stop()
        if self._close_progress is not None:
            self._close_progress.close()
            self._close_progress = None
        self._close_pending = False
        result, self._close_result = self._close_result, None
        assert result is not None
        self._host.setEnabled(True)
        self._host.done(result)
