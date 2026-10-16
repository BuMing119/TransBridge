"""Cooperative cancellation for read-only terminology queries."""

from __future__ import annotations

from threading import Event


class QueryCancelled(RuntimeError):
    """An obsolete read stopped at a safe checkpoint; no writes were rolled back."""


class QueryCancellation:
    def __init__(self) -> None:
        self._cancelled = Event()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise QueryCancelled("terminology query was cancelled")
