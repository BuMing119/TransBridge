"""Serialize read-only UI projections and discard superseded replies."""

from __future__ import annotations

from functools import partial

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot

from transbridge.ui.workers import ApiWorker


class LatestReadController(QObject):
    """Run pure readers off the GUI thread with at most one pending request.

    Readers receive captured data, never widgets. Invalidation does not wait for
    I/O; ApiWorker's registry retains an unfinished thread even if its view dies.
    """

    ready = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._generation = 0
        self._pending = None
        self._active = None
        self._worker = None
        self._reply = None
        self._error = ""

    @property
    def busy(self):
        return self._worker is not None

    def submit(self, request):
        self._generation += 1
        self._pending = (self._generation, request)
        if not self.busy:
            self._start()

    def invalidate(self):
        self._generation += 1
        self._pending = None

    def _start(self):
        self._active, self._pending = self._pending, None
        self._reply, self._error = None, ""
        worker = ApiWorker(partial(self._reader, self._active[1]), route_http_errors=False)
        self._worker = worker
        worker.result.connect(self._capture, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(self._capture_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @pyqtSlot(object)
    def _capture(self, reply):
        self._reply = reply

    @pyqtSlot(str)
    def _capture_error(self, error):
        self._error = error

    @pyqtSlot()
    def _finished(self):
        generation, request = self._active
        reply, error = self._reply, self._error
        self._worker = self._active = self._reply = None
        if self._pending is not None:
            self._start()
        elif generation == self._generation:
            if error:
                self.failed.emit(error)
            else:
                self.ready.emit(request, reply)
