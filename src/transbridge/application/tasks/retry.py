"""Explicit retry intents that always create a distinct TaskRuntime run."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Protocol

from transbridge.application.contracts import JobRef

from .history import TaskHistoryRecord
from .models import OwnerRef


class TaskRetryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskRetryContext:
    """Current authoritative context passed to feature-owned preflight logic."""

    actor: OwnerRef
    context_ref: str
    context_fingerprint: str

    def __post_init__(self) -> None:
        if not self.context_ref.strip():
            raise ValueError("retry context_ref must not be empty")
        if not self.context_fingerprint.strip():
            raise ValueError("retry context_fingerprint must not be empty")


class TaskRetryIntent(Protocol):
    """Feature handler that re-preflights and submits a new immutable JobSpec."""

    def __call__(self, previous: TaskHistoryRecord, context: TaskRetryContext) -> JobRef: ...


class TaskRetryIntentRegistry:
    """Routes retry by job type; it never mutates the previous terminal run."""

    def __init__(self) -> None:
        self._handlers: dict[str, TaskRetryIntent] = {}
        self._lock = threading.RLock()

    def register(self, job_type: str, handler: TaskRetryIntent) -> None:
        if not job_type.strip():
            raise ValueError("retry job_type must not be empty")
        with self._lock:
            existing = self._handlers.get(job_type)
            if existing is not None and existing is not handler:
                raise ValueError(f"retry intent for {job_type!r} is already registered")
            self._handlers[job_type] = handler

    def unregister(self, job_type: str, handler: TaskRetryIntent | None = None) -> None:
        with self._lock:
            existing = self._handlers.get(job_type)
            if existing is None or (handler is not None and existing is not handler):
                return
            self._handlers.pop(job_type, None)

    def supports(self, job_type: str) -> bool:
        with self._lock:
            return job_type in self._handlers

    def retry(self, previous: TaskHistoryRecord, context: TaskRetryContext) -> JobRef:
        if not previous.visible_to(context.actor):
            raise TaskRetryError("owner_mismatch", "retry actor cannot access the previous task")
        with self._lock:
            handler = self._handlers.get(previous.job_type)
        if handler is None:
            raise TaskRetryError(
                "retry_intent_unregistered",
                f"no retry intent is registered for {previous.job_type!r}",
            )

        # The feature handler owns current-input validation, idempotency policy,
        # and JobSpec construction.  The registry enforces only global identity
        # invariants that no handler may override.
        new_ref = handler(previous, context)
        new_run_id = new_ref.run_id or new_ref.job_id
        if new_run_id == previous.run_id or new_ref.job_id == previous.job_id:
            raise TaskRetryError(
                "retry_reused_run_id",
                "retry must submit a new task with a distinct Run ID",
            )
        if new_ref.owner_id != previous.owner.owner_id:
            raise TaskRetryError(
                "retry_owner_mismatch",
                "retry must remain in the previous task owner scope",
            )
        return new_ref
