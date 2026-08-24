"""Asynchronous close ownership for StringDetailDialog."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QProgressDialog


class StringDialogLifecycle:
    def __init__(self, host, *, workers: Callable[[], Iterable[object]]) -> None:
        self._host = host
        self._workers = workers
        self._close_pending = False
        self._close_progress: QProgressDialog | None = None

    def close_event(self, event) -> None:
        running = [worker for worker in self._workers() if worker.isRunning()]
        if not running:
            event.accept()
            return
        event.ignore()
        if self._close_pending:
            return
        self._close_pending = True
        self._host.setEnabled(False)
        progress = QProgressDialog("正在等待后台同步完成…", "", 0, 0, self._host)
        progress.setCancelButton(None)
        progress.setWindowTitle("正在关闭")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        self._close_progress = progress
        for worker in running:
            worker.finished.connect(self.finish_close_if_idle)

    def finish_close_if_idle(self) -> None:
        if any(worker.isRunning() for worker in self._workers()):
            return
        if self._close_progress is not None:
            self._close_progress.close()
            self._close_progress = None
        self._close_pending = False
        self._host.close()
