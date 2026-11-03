"""Deliver progressive task indexes without retaining a closed editor."""

from threading import Event
import weakref

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.ui.workers import ApiWorker


class _Cancellation:
    def __init__(self):
        self.event = Event()

    @property
    def is_cancelled(self):
        return self.event.is_set()

    def cancel(self, *_args):
        self.event.set()


class IndexDelivery(QObject):
    preview_ready = pyqtSignal(object)

    def __init__(
        self,
        loader,
        entries,
        controller,
        workers,
        generation,
        scope,
        *,
        plugin=None,
        snapshot=None,
        reading_entries=None,
    ):
        super().__init__()
        self._controller = weakref.ref(controller)
        self._workers = workers
        self._generation, self._scope = generation, scope
        self._cancellation = _Cancellation()
        controller.destroyed.connect(self._cancellation.cancel)
        self.preview_ready.connect(self._preview_loaded)
        worker = ApiWorker(
            self._build,
            loader,
            entries,
            plugin=plugin,
            snapshot=snapshot,
            reading_entries=reading_entries,
            cancellation=self._cancellation,
            on_preview=self.preview_ready.emit,
        )
        self._worker = worker
        self.setParent(worker)
        worker.result.connect(self._loaded)
        worker.error.connect(self._failed)
        worker.finished.connect(self._finished)
        workers.append(worker)
        worker.start()

    def cancel(self):
        self._cancellation.cancel()

    @staticmethod
    def _build(loader, entries, **kwargs):
        try:
            return loader.build(entries, **kwargs)
        except DialogueOrderCancelled:
            return None

    def _owner(self):
        owner = self._controller()
        return owner if owner is not None and not sip.isdeleted(owner) and not owner._closed else None

    @pyqtSlot(object)
    def _preview_loaded(self, index):
        if not self._cancellation.is_cancelled and (owner := self._owner()) is not None:
            owner._loaded(self._generation, self._scope, index, complete=False)

    @pyqtSlot(object)
    def _loaded(self, index):
        if index is not None and not self._cancellation.is_cancelled and (owner := self._owner()) is not None:
            owner._loaded(self._generation, self._scope, index)

    @pyqtSlot(str)
    def _failed(self, message):
        if not self._cancellation.is_cancelled and (owner := self._owner()) is not None:
            owner._failed(self._generation, message)

    @pyqtSlot()
    def _finished(self):
        if (owner := self._owner()) is not None and owner._delivery is self:
            owner._delivery = None
        self._workers.remove(self._worker)
        self._worker.deleteLater()
