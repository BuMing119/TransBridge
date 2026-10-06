"""Worker-owned delivery lifetime for terminology synchronization views."""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class SyncSignals(QObject):
    """Outlive a deleted view while outstanding workers finish their calls."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    activity = pyqtSignal(object)

    def __init__(self) -> None:
        # No QWidget parent: each runnable owns this bridge until it returns.
        super().__init__()
        self._closed = threading.Event()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @pyqtSlot()
    def close(self) -> None:
        self._closed.set()

    def post_activity(self, activity: object) -> None:
        if not self.closed:
            self.activity.emit(activity)


class SyncCall(QRunnable):
    def __init__(self, call, signals: SyncSignals) -> None:
        super().__init__()
        self._call = call
        self._signals = signals

    def run(self) -> None:
        if self._signals.closed:
            return
        try:
            value = self._call()
        except Exception as exc:  # noqa: BLE001 - application failure is delivered to the owning view
            if not self._signals.closed:
                self._signals.failed.emit(exc)
            else:
                logger.warning("Terminology synchronization call failed after its view closed", exc_info=True)
        else:
            if not self._signals.closed:
                self._signals.completed.emit(value)
