"""Deliver progressive task indexes without retaining a closed editor."""

import weakref

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from transbridge.ui.workers import ApiWorker


class IndexDelivery(QObject):
    preview_ready = pyqtSignal(object)

    def __init__(self, loader, entries, controller, workers, generation, scope, *, plugin=None, snapshot=None):
        super().__init__()
        self._controller = weakref.ref(controller)
        self._workers = workers
        self._generation, self._scope = generation, scope
        self.preview_ready.connect(self._preview_loaded)
        worker = ApiWorker(loader.build, entries, plugin=plugin, snapshot=snapshot, on_preview=self.preview_ready.emit)
        self._worker = worker
        self.setParent(worker)
        worker.result.connect(self._loaded)
        worker.error.connect(self._failed)
        worker.finished.connect(self._finished)
        workers.append(worker)
        worker.start()

    def _owner(self):
        owner = self._controller()
        return owner if owner is not None and not sip.isdeleted(owner) and not owner._closed else None

    @pyqtSlot(object)
    def _preview_loaded(self, index):
        if (owner := self._owner()) is not None:
            owner._loaded(self._generation, self._scope, index, complete=False)

    @pyqtSlot(object)
    def _loaded(self, index):
        if (owner := self._owner()) is not None:
            owner._loaded(self._generation, self._scope, index)

    @pyqtSlot(str)
    def _failed(self, message):
        if (owner := self._owner()) is not None:
            owner._failed(self._generation, message)

    @pyqtSlot()
    def _finished(self):
        self._workers.remove(self._worker)
        self._worker.deleteLater()
