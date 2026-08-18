"""Session aggregate with owner/revision-gated runtime event application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import RLock
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.tasks.models import TERMINAL_STATES, JobState, OwnerRef

from .models import ApprovalState, PendingApproval, SessionJobRef, SessionSnapshot


class SessionEventKind(StrEnum):
    JOB_STATE = "job_state"
    APPROVAL = "approval"
    MESSAGE = "message"


@dataclass(frozen=True, slots=True)
class SessionRuntimeEvent:
    kind: SessionEventKind
    owner: OwnerRef
    run_id: str
    aggregate_revision: int
    sequence: int
    job_id: str | None = None
    job_state: JobState | None = None
    approval_id: str | None = None
    approval_state: ApprovalState | None = None
    request_hash: str | None = None
    payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("Session runtime event run_id must not be empty")
        if self.aggregate_revision < 0 or self.sequence < 0:
            raise ValueError("Session event revision and sequence must not be negative")
        if self.kind is SessionEventKind.JOB_STATE and (self.job_id is None or self.job_state is None):
            raise ValueError("job state event requires job_id and job_state")
        if self.kind is SessionEventKind.APPROVAL and not all((
            self.approval_id,
            self.approval_state,
            self.request_hash,
        )):
            raise ValueError("approval event requires approval_id, approval_state, and request_hash")


@dataclass(frozen=True, slots=True)
class EventApplication:
    applied: bool
    revision: int
    diagnostic: Diagnostic | None = None


class SessionAggregate:
    def __init__(self, snapshot: SessionSnapshot) -> None:
        self._snapshot = snapshot
        self._listeners: dict[int, Callable[[SessionSnapshot], None]] = {}
        self._next_listener = 0
        self._lock = RLock()

    @property
    def ref(self):
        return self._snapshot.ref

    @property
    def owner(self) -> OwnerRef:
        return self._snapshot.owner

    @property
    def revision(self) -> int:
        return self._snapshot.revision

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return self._snapshot

    def replace_snapshot(self, snapshot: SessionSnapshot, *, expected_revision: int) -> SessionSnapshot:
        with self._lock:
            if snapshot.ref != self.ref or snapshot.owner != self.owner:
                raise ValueError("replacement Session snapshot changes aggregate identity or owner")
            if expected_revision != self.revision:
                raise ValueError("Session aggregate revision conflict")
            projected = replace(snapshot, revision=self.revision + 1)
            self._snapshot = projected
            self._notify(projected)
            return projected

    def apply_runtime_event(self, event: SessionRuntimeEvent) -> EventApplication:
        with self._lock:
            mismatch = self._event_mismatch(event)
            if mismatch is not None:
                return EventApplication(False, self.revision, mismatch)
            if event.kind is SessionEventKind.APPROVAL:
                return self._apply_approval_event(event)
            if event.kind is not SessionEventKind.JOB_STATE:
                return EventApplication(
                    False,
                    self.revision,
                    _ignored("SESSION_EVENT_KIND_NOT_REHYDRATABLE", "The event kind has no aggregate reducer."),
                )
            jobs = list(self._snapshot.jobs)
            index = next((i for i, item in enumerate(jobs) if item.ref.job_id == event.job_id), None)
            if index is None:
                return EventApplication(
                    False,
                    self.revision,
                    _ignored("SESSION_EVENT_JOB_UNKNOWN", "The event JobRef is not owned by this Session."),
                )
            current = jobs[index]
            if current.ref.run_id != event.run_id:
                return EventApplication(
                    False,
                    self.revision,
                    _ignored("SESSION_EVENT_RUN_MISMATCH", "The event run does not match the Session JobRef."),
                )
            if event.sequence <= current.last_sequence:
                return EventApplication(
                    False,
                    self.revision,
                    _ignored("SESSION_EVENT_SEQUENCE_STALE", "The event sequence is stale or duplicated."),
                )
            if current.state in TERMINAL_STATES and event.job_state != current.state:
                return EventApplication(
                    False,
                    self.revision,
                    _ignored(
                        "SESSION_EVENT_TERMINAL_REGRESSION",
                        "A terminal Session job cannot transition back to a non-terminal state.",
                    ),
                )
            jobs[index] = SessionJobRef(
                current.ref,
                event.job_state,
                event.sequence,
                current.recoverable,
                current.reason,
            )
            projected = replace(
                self._snapshot,
                jobs=tuple(jobs),
                revision=self.revision + 1,
            )
            self._snapshot = projected
            self._notify(projected)
            return EventApplication(True, projected.revision)

    def _apply_approval_event(self, event: SessionRuntimeEvent) -> EventApplication:
        approvals = list(self._snapshot.approvals)
        index = next((i for i, item in enumerate(approvals) if item.approval_id == event.approval_id), None)
        if index is None:
            return EventApplication(
                False,
                self.revision,
                _ignored("SESSION_EVENT_APPROVAL_UNKNOWN", "The approval is not owned by this Session."),
            )
        current = approvals[index]
        if current.run_id != event.run_id:
            return EventApplication(
                False,
                self.revision,
                _ignored("SESSION_EVENT_RUN_MISMATCH", "The event run does not match the pending approval."),
            )
        if current.request_hash != event.request_hash:
            return EventApplication(
                False,
                self.revision,
                _ignored(
                    "SESSION_EVENT_APPROVAL_HASH_MISMATCH",
                    "The event request does not match the pending approval.",
                ),
            )
        if current.state is not ApprovalState.PENDING:
            return EventApplication(
                False,
                self.revision,
                _ignored("SESSION_EVENT_APPROVAL_CONSUMED", "The pending approval was already consumed."),
            )
        approvals[index] = PendingApproval(
            current.approval_id,
            current.owner_id,
            current.session_id,
            current.run_id,
            current.request_hash,
            self.revision + 1,
            event.approval_state,
        )
        projected = replace(
            self._snapshot,
            approvals=tuple(approvals),
            revision=self.revision + 1,
        )
        self._snapshot = projected
        self._notify(projected)
        return EventApplication(True, projected.revision)

    def subscribe(self, listener: Callable[[SessionSnapshot], None]) -> Callable[[], None]:
        with self._lock:
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(token, None)

        return unsubscribe

    @property
    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)

    def close(self) -> None:
        with self._lock:
            self._listeners.clear()

    def _event_mismatch(self, event: SessionRuntimeEvent) -> Diagnostic | None:
        if not self.owner.same_scope(event.owner):
            return _ignored("SESSION_EVENT_OWNER_MISMATCH", "A late event belongs to a different Session owner.")
        if event.aggregate_revision != self.revision:
            return _ignored("SESSION_EVENT_REVISION_MISMATCH", "A late event targets a stale Session revision.")
        return None

    def _notify(self, snapshot: SessionSnapshot) -> None:
        for listener in tuple(self._listeners.values()):
            try:
                listener(snapshot)
            except Exception:
                continue


def _ignored(code: str, message: str) -> Diagnostic:
    return Diagnostic(code, message, DiagnosticSeverity.WARNING)


__all__ = [
    "EventApplication",
    "SessionAggregate",
    "SessionEventKind",
    "SessionRuntimeEvent",
]
