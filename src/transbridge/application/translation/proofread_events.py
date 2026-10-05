"""Bounded, thread-safe user-facing progress events for proofreading."""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time

logger = logging.getLogger(__name__)


class ProofreadEventLog:
    """Keep routine progress sparse while delivering recovery events immediately."""

    def __init__(self, callback: Callable[[str], None] | None) -> None:
        self._callback = callback
        self._lock = threading.RLock()
        self._progress: dict[str, tuple[int, int, float]] = {}

    def emit(self, message: str) -> None:
        if self._callback is None:
            return
        with self._lock:
            try:
                self._callback(message)
            except Exception:
                logger.warning("Proofreading event callback failed; processing will continue.", exc_info=True)

    def progress(self, phase: str, completed: int, total: int) -> None:
        if total <= 0 or completed <= 0:
            return
        with self._lock:
            now = time.monotonic()
            previous, previous_bucket, last_time = self._progress.get(phase, (0, 0, now))
            bucket = min(10, completed * 10 // total)
            if completed <= previous:
                return
            if bucket <= previous_bucket and now - last_time < 5:
                self._progress.setdefault(phase, (previous, previous_bucket, last_time))
                return
            self._progress[phase] = (completed, bucket, now)
            self.emit(f"{phase}进度 {completed}/{total} 条")
