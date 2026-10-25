"""Incremental equivalent of the stream's adaptive repetition checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class _Period:
    width: int
    minimum: int
    streak: int = 0
    start: int = -1
    length: int = 0
    frozen: bool = False


class StreamRepetitionDetector:
    """Consume each character once, retaining only ten characters of history.

    The legacy regex checks the first qualifying run for each period, even if a
    later run is longer. Keep that behavior and its greedy whole-block length.
    Its 1/2/3-character alternation always chooses the single-character branch.
    """

    def __init__(self, max_orig_repeat: int = 0) -> None:
        self._threshold = max(80, max_orig_repeat * 2 + 20)
        self._phrase_threshold = max(200, max_orig_repeat * 1.5 + 20)
        self._periods = [_Period(w, n) for w, n in [(4, 20), (5, 15), (6, 12), (7, 10), (8, 10), (9, 8), (10, 8)]]
        self._tail = ""
        self._offset = 0
        self._newline = -1
        self._run = 0
        self._maximum = 0
        self._maximum_start = -1

    def feed(self, chunk: str) -> tuple[bool, int]:
        for char in chunk:
            index = self._offset
            tail = self._tail
            if char == "\n":
                self._newline = index
                self._run = 0
            else:
                self._run = self._run + 1 if tail and char == tail[-1] else 1
                if self._run > self._maximum:
                    self._maximum = self._run
                    self._maximum_start = index - self._run + 1
            for period in self._periods:
                if period.frozen:
                    continue
                width = period.width
                if index - self._newline > width and len(tail) >= width and char == tail[-width]:
                    period.streak += 1
                    if period.start < 0 and period.streak >= width * (period.minimum - 1):
                        period.start = index - period.streak - width + 1
                    if period.start >= 0:
                        period.length = ((index - period.start + 1) // width) * width
                else:
                    if period.start >= 0:
                        period.frozen = True
                    period.streak = 0
            self._tail = (tail + char)[-10:]
            self._offset += 1
        if self._maximum > self._threshold:
            return True, self._maximum_start
        for period in self._periods:
            if period.length > self._phrase_threshold:
                return True, period.start
        return False, -1
