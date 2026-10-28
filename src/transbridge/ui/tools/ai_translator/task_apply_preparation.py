"""GUI-thread delivery after a detached operation has fully finished."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSlot


class ApplyDelivery(QObject):
    """Retain the worker until finished; deliver once on the creating GUI thread."""

    def __init__(self, worker, completed):
        super().__init__()
        self.worker = worker
        self.completed = completed
        self.value = None
        self.error = None
        worker.result.connect(self._result)
        worker.error.connect(self._error)
        worker.finished.connect(self._finished)

    @pyqtSlot(object)
    def _result(self, value):
        self.value = value

    @pyqtSlot(str)
    def _error(self, message):
        self.error = message

    @pyqtSlot()
    def _finished(self):
        try:
            self.completed(self.value, self.error)
        finally:
            self.worker.deleteLater()
            self.deleteLater()
