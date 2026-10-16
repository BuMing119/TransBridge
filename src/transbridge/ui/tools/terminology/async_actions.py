"""Deliver scoped application calls back to Qt, dropping superseded or closed reads."""

from __future__ import annotations

from weakref import finalize

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal

from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled


class _Signals(QObject):
    completed = pyqtSignal(int, object, object)


class _ReadLifetime:
    """Finalizer state contains cancellation tokens, never Qt owners or callbacks."""

    def __init__(self):
        self.closed = False
        self.tokens = set()

    def close(self):
        self.closed = True
        tokens = tuple(self.tokens)
        self.tokens.clear()
        for token in tokens:
            token.cancel()


class _Call(QRunnable):
    def __init__(self, identity, command, signals, cancelled):
        super().__init__()
        self.identity = identity
        self.command = command
        self.signals = signals
        self.cancelled = cancelled

    def run(self):
        if self.cancelled.is_cancelled:
            self.signals.completed.emit(self.identity, None, None)
            return
        result, error = None, None
        try:
            result = self.command()
        except QueryCancelled as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - application boundary, delivered with original cause
            error = exc
        self.signals.completed.emit(self.identity, result, error)


class AsyncActionRunner(QObject):
    def __init__(self, parent=None, *, on_error, thread_pool=None):
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.completed.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._on_error = on_error
        self._pending = {}
        self._sequence = 0
        self._closed = False
        self._lifetime = _ReadLifetime()
        self.destroyed.connect(finalize(self, self._lifetime.close))

    def run(self, command, on_success, *, key=None, on_error=None):
        """Queue a command; once started, its write is allowed to finish."""
        self._submit(command, on_success, key=key, on_error=on_error)

    def run_read(self, command, on_success, *, key=None, on_error=None, on_cancelled=None):
        """Pass a cooperative cancellation token to an explicitly read-only call."""
        return self._submit(
            command, on_success, key=key, on_error=on_error, cooperative=True, on_cancelled=on_cancelled
        )

    def _submit(self, command, on_success, *, key, on_error, cooperative=False, on_cancelled=None):
        if self._closed or self._lifetime.closed:
            return
        if key is not None:
            for identity, (old_key, cancelled, _success, _failure, _settled) in tuple(self._pending.items()):
                if old_key == key:
                    cancelled.cancel()
                    self._lifetime.tokens.discard(cancelled)
                    del self._pending[identity]
        self._sequence += 1
        cancelled = QueryCancellation()
        self._lifetime.tokens.add(cancelled)
        self._pending[self._sequence] = (key, cancelled, on_success, on_error or self._on_error, on_cancelled)
        if cooperative:
            read = command

            def command():
                return read(cancelled)

        self._pool.start(_Call(self._sequence, command, self._signals, cancelled))
        return cancelled

    def close(self):
        self._closed = True
        self._lifetime.close()
        self._pending.clear()

    def _complete(self, identity, value, error):
        pending = self._pending.pop(identity, None)
        if self._closed or pending is None:
            return
        _key, cancelled, success, failure, settled = pending
        self._lifetime.tokens.discard(cancelled)
        if cancelled.is_cancelled or isinstance(error, QueryCancelled):
            if settled is not None:
                settled()
            return
        if error is not None:
            failure(error)
        else:
            success(value)
