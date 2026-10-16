"""Debounced pure-data search with dataset, query and widget-lifetime isolation."""

from threading import Event

from PyQt6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal

from .row_search import RowMatches, build_search_index, match_search_index


class _Jobs:
    def __init__(self):
        self.closed = Event()
        self.index = Event()
        self.query = Event()

    def close(self, *_args):
        self.closed.set()
        self.index.set()
        self.query.set()


class _Signals(QObject):
    finished = pyqtSignal(str, int, int, object, object)


class _SearchCall(QRunnable):
    def __init__(self, kind, version, generation, operation, cancelled, closed, signals):
        super().__init__()
        self.kind, self.version, self.generation = kind, version, generation
        self.operation, self.cancelled, self.closed, self.signals = operation, cancelled, closed, signals

    def run(self):
        if self.cancelled.is_set() or self.closed.is_set():
            return
        result, error = None, None
        try:
            result = self.operation()
        except Exception as exc:  # noqa: BLE001 - preserve the search failure for the owning view
            error = exc
        if not self.cancelled.is_set() and not self.closed.is_set():
            self.signals.finished.emit(self.kind, self.version, self.generation, result, error)


class AsyncRowFilter(QObject):
    matches_changed = pyqtSignal(object)
    status_changed = pyqtSignal(bool, str)

    def __init__(self, parent, *, thread_pool=None, small_limit=256, debounce_ms=200):
        super().__init__(parent)
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._small_limit = small_limit
        self._jobs = _Jobs()
        self._signals = _Signals()
        self._signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._search)
        self._rows = ()
        self._index = None
        self._text, self._facets = "", ()
        self._version = self._generation = 0
        self._pending = False
        self.last_error = None
        parent.destroyed.connect(self._jobs.close)
        window = parent.window() if hasattr(parent, "window") else parent
        for watched in (parent,) if window is parent else (parent, window):
            watched.installEventFilter(self)

    @property
    def is_pending(self):
        return self._pending

    def set_rows(self, rows, document):
        if self._jobs.closed.is_set():
            return
        self._jobs.index.set()
        self._cancel_query()
        self._jobs.index = Event()
        self._version += 1
        self._rows, self._index, self.last_error = tuple(rows), None, None
        self.matches_changed.emit(RowMatches.all(0 if self._filtered else len(self._rows)))
        self._status(self._filtered)
        rows, cancelled = self._rows, self._jobs.index

        def operation():
            return build_search_index(rows, document, cancelled)

        if len(rows) <= self._small_limit:
            try:
                self._index = operation()
            except Exception as exc:  # noqa: BLE001 - same visible failure contract as background indexing
                self._error(exc)
                return
            self._search()
        else:
            self._start("index", operation, cancelled)

    def set_query(self, text, facets=(), *, debounce=True):
        if self._jobs.closed.is_set():
            return
        self._text, self._facets = text.strip().casefold(), tuple(facets)
        self._cancel_query()
        if not self._filtered:
            self.matches_changed.emit(RowMatches.all(len(self._rows)))
            self._status(False)
        elif self.last_error is not None:
            self._error(self.last_error)
        elif len(self._rows) <= self._small_limit:
            self._search()
        else:
            self._status(True)
            if debounce:
                self._timer.start()
            else:
                self._search()

    @property
    def _filtered(self):
        return bool(self._text or any(self._facets))

    def _cancel_query(self):
        self._timer.stop()
        self._jobs.query.set()
        self._jobs.query = Event()
        self._generation += 1

    def _start(self, kind, operation, cancelled):
        self._pool.start(
            _SearchCall(kind, self._version, self._generation, operation, cancelled, self._jobs.closed, self._signals)
        )

    def _search(self):
        if self._jobs.closed.is_set() or self._index is None:
            return
        if not self._filtered:
            self._status(False)
            return
        index, text, facets, cancelled = self._index, self._text, self._facets, self._jobs.query

        def operation():
            return match_search_index(index, text, facets, cancelled)

        if len(self._rows) <= self._small_limit:
            self.matches_changed.emit(operation())
            self._status(False)
        else:
            self._status(True)
            self._start("query", operation, cancelled)

    def _finished(self, kind, version, generation, result, error):
        if self._jobs.closed.is_set() or version != self._version:
            return
        if kind == "query" and generation != self._generation:
            return
        if error is not None:
            self._error(error)
        elif kind == "index":
            self._index = result
            if not self._timer.isActive():
                self._search()
        elif result is not None:
            self.matches_changed.emit(result)
            self._status(False)

    def _status(self, pending, message=""):
        self._pending = pending
        self.status_changed.emit(pending, message)

    def _error(self, error):
        self.last_error = error
        self._status(False, f"搜索未完成：{error}。请重新载入后重试。")

    def close(self):
        self._jobs.close()
        self._timer.stop()
        self._pending = False

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Close:
            self.close()
        return super().eventFilter(watched, event)
