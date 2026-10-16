"""Cancellable query delivery that outlives closed or deleted table models safely."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled


class QuerySignals(QObject):
    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, object)


class QueryRunnable(QRunnable):
    def __init__(self, generation: int, load: Callable, signals: QuerySignals, cancelled: QueryCancellation) -> None:
        super().__init__()
        self._generation, self._load = generation, load
        self._signals, self._cancelled = signals, cancelled

    def run(self) -> None:
        if self._cancelled.is_cancelled:
            return
        try:
            page = self._load(self._cancelled)
        except QueryCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - query-port boundary
            if not self._cancelled.is_cancelled:
                self._signals.failed.emit(self._generation, exc)
            return
        if not self._cancelled.is_cancelled:
            self._signals.succeeded.emit(self._generation, page)


class PageQueryDispatch:
    def __init__(self, pool: QThreadPool) -> None:
        self._pool = pool
        self.signals = QuerySignals()
        self._cancelled = QueryCancellation()
        self.closed = False

    def cancel(self) -> None:
        self._cancelled.cancel()
        self._cancelled = QueryCancellation()

    def start(self, generation: int, load: Callable) -> None:
        self.start_read(generation, lambda _token: load())

    def start_read(self, generation: int, load: Callable) -> None:
        if not self.closed:
            self._pool.start(QueryRunnable(generation, load, self.signals, self._cancelled))

    def close(self) -> None:
        self.closed = True
        self._cancelled.cancel()
