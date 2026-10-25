"""A bounded progress handoff from a categorized worker to the GUI timer."""

from __future__ import annotations

from threading import Lock

ProgressUpdate = tuple[int, int, str]


class LatestProgress:
    """Retain only the latest update, without queueing one Qt event per entry."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: ProgressUpdate | None = None
        self._closed = False

    def publish(self, current: int, total: int, message: str = "") -> None:
        with self._lock:
            if not self._closed:
                self._latest = (current, total, message)

    def take(self) -> ProgressUpdate | None:
        with self._lock:
            update, self._latest = self._latest, None
            return update

    def finish(self) -> ProgressUpdate | None:
        """Atomically close the handoff and return the last unrendered update."""
        with self._lock:
            self._closed = True
            update, self._latest = self._latest, None
            return update
