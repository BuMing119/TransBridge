"""Immutable project-name progress with one coordinator and concurrent source updates."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
import re
from threading import RLock


@dataclass(frozen=True, slots=True)
class ProjectNameSourceProgress:
    key: str
    current: int
    total: int
    detail: str


@dataclass(frozen=True, slots=True)
class ProjectNameProgress:
    phase: str
    current: int
    total: int
    message: str
    sources: tuple[ProjectNameSourceProgress, ...]
    name_count: int = 0
    occurrence_count: int = 0
    reused_count: int = 0


_COUNTER_PREFIX = re.compile(r"^\[\s*\d+%\]\s*\d+/\d+\s*\|\s*")


class ProjectNameProgressReporter:
    """Fix denominators before each phase and serialize absolute source counters."""

    def __init__(self, source_keys: Iterable[str], callback: Callable[[ProjectNameProgress], None] | None = None):
        self._keys = tuple(dict.fromkeys(source_keys))
        self._callback = callback
        self._lock = RLock()
        self._snapshot: ProjectNameProgress | None = None

    @property
    def snapshot(self) -> ProjectNameProgress | None:
        with self._lock:
            return self._snapshot

    def begin(
        self,
        phase: str,
        totals: Mapping[str, int],
        message: str,
        *,
        details: Mapping[str, str] | None = None,
        name_count: int | None = None,
        occurrence_count: int | None = None,
        reused_count: int | None = None,
    ) -> None:
        """Only the coordinator starts phases; source callbacks cannot change them."""
        with self._lock:
            previous = self._snapshot
            if previous is not None and previous.phase in {"cancelled", "done"}:
                return
            rows = tuple(
                ProjectNameSourceProgress(key, 0, max(0, totals.get(key, 0)), (details or {}).get(key, message))
                for key in self._keys
            )
            counts = {
                key: value if value is not None else getattr(previous, key, 0)
                for key, value in (
                    ("name_count", name_count),
                    ("occurrence_count", occurrence_count),
                    ("reused_count", reused_count),
                )
            }
            self._emit(ProjectNameProgress(phase, 0, sum(row.total for row in rows), message, rows, **counts))

    def update(self, phase: str, key: str, *, current: int | None = None, detail: str | None = None) -> None:
        with self._lock:
            previous = self._snapshot
            if previous is None or previous.phase != phase or phase in {"done", "cancelled"}:
                return
            rows = tuple(
                replace(
                    row,
                    current=row.current if current is None else max(row.current, min(row.total, max(0, current))),
                    detail=row.detail if detail is None else _COUNTER_PREFIX.sub("", detail),
                )
                if row.key == key
                else row
                for row in previous.sources
            )
            self._emit(replace(previous, current=sum(row.current for row in rows), sources=rows))

    def finish(self, totals: Mapping[str, int], message: str) -> None:
        """All names have settled; failures remain failures in the result contract."""
        with self._lock:
            previous = self._snapshot
            if previous is None or previous.phase in {"cancelled", "done"}:
                return
            rows = tuple(
                ProjectNameSourceProgress(key, max(0, totals.get(key, 0)), max(0, totals.get(key, 0)), message)
                for key in self._keys
            )
            total = sum(row.total for row in rows)
            self._emit(replace(previous, phase="done", current=total, total=total, message=message, sources=rows))

    def cancel(self) -> None:
        with self._lock:
            previous = self._snapshot
            if previous is not None and previous.phase not in {"cancelled", "done"}:
                self._emit(replace(previous, phase="cancelled", message="项目名称准备已取消"))

    def _emit(self, snapshot: ProjectNameProgress) -> None:
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        # Emit under the same lock as aggregation so queued UI signals preserve
        # snapshot order even when two model batches finish simultaneously.
        if self._callback is not None:
            self._callback(snapshot)
