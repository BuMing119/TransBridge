"""Serialize one window's draft writes without blocking the Qt event loop."""

from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QObject

from .async_actions import AsyncActionRunner


class DraftWriteQueue(QObject):
    """Queued writes may be withdrawn on close; an executing commit is not interrupted."""

    def __init__(self, parent=None, *, on_started, on_saved, on_error):
        super().__init__(parent)
        self._on_started = on_started
        self._on_saved = on_saved
        self._on_error = on_error
        self._commands = deque()
        self._running = False
        self._closed = False
        self._runner = AsyncActionRunner(self, on_error=self._failed)
        self.destroyed.connect(self._runner.close)

    def submit(self, command) -> None:
        if self._closed:
            return
        self._commands.append(command)
        self._start_next()

    def close(self) -> None:
        self._closed = True
        self._commands.clear()
        self._runner.close()

    def _start_next(self) -> None:
        if self._closed or self._running or not self._commands:
            return
        self._running = True
        self._on_started()
        self._runner.run(self._commands.popleft(), self._saved)

    def _saved(self, value) -> None:
        self._running = False
        if not self._closed:
            self._on_saved(value)
            self._start_next()

    def _failed(self, error) -> None:
        self._running = False
        if not self._closed:
            self._on_error(error)
            self._start_next()
