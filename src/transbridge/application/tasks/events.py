"""Read-only task events and disposable subscriptions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import threading

from .models import JobSnapshot, JobState


class JobEventType(StrEnum):
    CREATED = "created"
    STATE_CHANGED = "state_changed"
    DIAGNOSTIC = "diagnostic"
    IGNORED = "ignored"
    FINISHED = "finished"
    PROGRESS = "progress"


@dataclass(frozen=True, slots=True)
class JobEvent:
    event_type: JobEventType
    snapshot: JobSnapshot
    sequence: int
    revision: int
    occurred_at: datetime
    previous_state: JobState | None = None
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class TaskEventFilter:
    run_id: str | None = None
    owner_id: str | None = None
    event_types: frozenset[JobEventType] = frozenset()

    def matches(self, event: JobEvent) -> bool:
        if self.run_id is not None and event.snapshot.ref.run_id != self.run_id:
            return False
        if self.owner_id is not None and event.snapshot.owner.owner_id != self.owner_id:
            return False
        return not self.event_types or event.event_type in self.event_types


class Subscription:
    """Idempotent handle that removes the exact registered callback wrapper."""

    def __init__(self, token: int, close_callback: Callable[[int], None]) -> None:
        self._token = token
        self._close_callback = close_callback
        self._lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close_callback(self._token)

    dispose = close
