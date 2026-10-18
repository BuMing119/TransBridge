"""Serialize one window's draft writes without blocking the Qt event loop."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from .async_actions import AsyncActionRunner


class DraftWriteDiscarded(RuntimeError):
    """The user explicitly withdrew a write before it started."""


@dataclass(frozen=True)
class _Write:
    command: object
    saved: object = None
    failed: object = None


class DraftWriteQueue(QObject):
    """Serialize accepted writes and expose their lifetime to the close guard."""

    pending_changed = pyqtSignal(int)
    drained = pyqtSignal(bool)

    def __init__(self, parent=None, *, on_started, on_saved, on_error):
        super().__init__(parent)
        self._on_started = on_started
        self._on_saved = on_saved
        self._on_error = on_error
        self._commands = deque()
        self._running = False
        self._current = None
        self._failed_in_batch = False
        self.last_error = None
        self.failure_count = 0
        self._closed = False
        self._runner = AsyncActionRunner(self, on_error=self._failed)
        self.destroyed.connect(self._runner.close)

    @property
    def pending_count(self) -> int:
        return len(self._commands) + int(self._running)

    def submit(self, command, *, on_saved=None, on_error=None) -> None:
        if self._closed:
            return
        if not self.pending_count:
            self._failed_in_batch = False
            self.last_error = None
            self.failure_count = 0
        self._commands.append(_Write(command, on_saved, on_error))
        self.pending_changed.emit(self.pending_count)
        self._start_next()

    def discard_pending(self) -> None:
        """Explicitly withdraw waiting operations; never interrupt an active commit."""
        discarded = tuple(self._commands)
        self._commands.clear()
        for write in discarded:
            if write.failed is not None:
                write.failed(DraftWriteDiscarded("已按要求放弃尚未开始的修改。"))
        self.pending_changed.emit(self.pending_count)

    def close(self) -> None:
        self._closed = True
        self._commands.clear()
        self._runner.close()

    def _start_next(self) -> None:
        if self._closed or self._running or not self._commands:
            return
        self._running = True
        self._current = self._commands.popleft()
        self._on_started()
        self._runner.run(self._current.command, self._saved)

    def _saved(self, value) -> None:
        if not self._closed:
            self._on_saved(value)
            if self._current.saved is not None:
                self._current.saved(value)
            self._settled()

    def _failed(self, error) -> None:
        if not self._closed:
            self._failed_in_batch = True
            self.last_error = error
            self.failure_count += 1
            self._on_error(error)
            if self._current.failed is not None:
                self._current.failed(error)
            self._settled()

    def _settled(self) -> None:
        self._running = False
        self._current = None
        if self._commands:
            self._start_next()
            self.pending_changed.emit(self.pending_count)
        else:
            self.pending_changed.emit(0)
            self.drained.emit(not self._failed_in_batch)
