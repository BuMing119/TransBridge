"""Application ownership of API threads through GUI delivery and native completion."""

from __future__ import annotations

from weakref import WeakSet

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QObject, Qt, QTimer, pyqtSignal, pyqtSlot


class ApiWorkerRegistry(QObject):
    """Keep threads alive and let shutdown await all consumers, including child windows."""

    _native_finished = pyqtSignal(object)
    _settled = pyqtSignal(object)

    def __init__(self, application: QCoreApplication) -> None:
        super().__init__(application)
        self._active: dict[int, object] = {}
        self._observed: WeakSet = WeakSet()
        self._settling: set[int] = set()
        self._reap_timer = QTimer(self)
        self._reap_timer.setInterval(10)
        self._reap_timer.timeout.connect(self._reap_finished)
        self._native_finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._settled.connect(self._release_stopped, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self) -> bool:
        # Do not use isRunning alone: a stopped thread may still have queued
        # results that update the project or submit another worker.
        return bool(self._active)

    def is_pending(self, worker) -> bool:
        return id(worker) in self._active

    def retain(self, worker) -> None:
        identity = id(worker)
        self._active[identity] = worker
        if worker not in self._observed:
            self._observed.add(worker)
            # Forward only an integer identity. Never run Python cleanup from
            # QObject.destroyed, or capture the thread in its own callback.
            worker.finished.connect(lambda: self._native_finished.emit(identity), Qt.ConnectionType.DirectConnection)

    def release(self, worker) -> None:
        self._active.pop(id(worker), None)
        self._settling.discard(id(worker))

    @pyqtSlot(object)
    def _finished(self, identity: int) -> None:
        # Let other finished handlers (including those connected after start)
        # deliver their state changes before a close can take its final snapshot.
        self._settled.emit(identity)

    @pyqtSlot(object)
    def _release_stopped(self, identity: int) -> None:
        worker = self._active.get(identity)
        if worker is None:
            self._settling.discard(identity)
        elif sip.isdeleted(worker) or worker.wait(0):
            self.release(worker)
        else:
            # isRunning() can already be False inside a finished DirectConnection
            # handler. wait(0) tests native termination without blocking the GUI.
            self._settling.add(identity)
            self._reap_timer.start()
        if not self._settling:
            self._reap_timer.stop()

    def _reap_finished(self) -> None:
        for identity in tuple(self._settling):
            self._release_stopped(identity)


def get_api_worker_registry() -> ApiWorkerRegistry:
    application = QCoreApplication.instance()
    if application is None:
        raise RuntimeError("API worker registry requires an active Qt application")
    registry = getattr(application, "_transbridge_api_worker_registry", None)
    if registry is None:
        registry = ApiWorkerRegistry(application)
        application._transbridge_api_worker_registry = registry
    return registry
