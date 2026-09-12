"""Generation-scoped user submissions shared by typed and programmatic input."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer


class SubmissionBinding:
    def __init__(self, *, interrupt: Callable[[], None], append: Callable[[str], None], start: Callable[[str], None]):
        self._interrupt = interrupt
        self._append = append
        self._start = start
        self._generation = 0
        self._closed = False

    def submit(self, text: str) -> None:
        if self._closed or not text.strip():
            return
        self.invalidate()
        generation = self._generation
        self._interrupt()
        self._append(text.strip())
        QTimer.singleShot(0, lambda: self._start_if_current(generation, text.strip()))

    def invalidate(self) -> None:
        self._generation += 1

    def close(self) -> None:
        self._closed = True
        self.invalidate()

    def _start_if_current(self, generation: int, text: str) -> None:
        if not self._closed and generation == self._generation:
            self._start(text)
