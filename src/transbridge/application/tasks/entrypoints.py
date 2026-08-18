"""Production task entrypoint bridge: submit -> JobRef -> terminal outcome.

This is the AWAITING_TASK production path: a translator/post-process/Graph
entrypoint submits a workload, receives a :class:`Deferred[JobRef]`, and the
session controller coordinates completion through runtime events.  Late events
from a previous session are audited but never accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from typing import Any

from transbridge.application.contracts import (
    Deferred,
    Diagnostic,
    ErrorCategory,
    JobRef,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)

from .controls import CancellationToken
from .events import JobEvent, JobEventType, Subscription, TaskEventFilter
from .models import TERMINAL_STATES, JobSnapshot, JobSpec, JobState, OwnerRef
from .runtime import TaskRuntime


@dataclass(frozen=True, slots=True)
class TerminalOutcome:
    snapshot: JobSnapshot
    outcome: OperationOutcome
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.snapshot.ref.job_id,
            "run_id": self.snapshot.ref.run_id,
            "state": self.snapshot.state.value,
            "outcome": self.outcome.value,
            "revision": self.snapshot.revision,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


class TaskWaitTimeout(TimeoutError):
    """A wait deadline expired while the task remained non-terminal."""

    def __init__(self, snapshot: JobSnapshot, timeout: float) -> None:
        super().__init__(f"task {snapshot.ref.job_id} did not finish within {timeout:.3f}s")
        self.snapshot = snapshot
        self.timeout = timeout


class RuntimeTaskBridge:
    """Owns submission, terminal waiting and capability-gated controls."""

    def __init__(self, runtime: TaskRuntime) -> None:
        self._runtime = runtime

    def submit(
        self,
        specification: JobSpec,
        owner: OwnerRef,
        workload: Callable[[CancellationToken], object],
    ) -> Deferred[JobRef]:
        """AWAITING_TASK entry: returns a deferred JobRef, never a fake sync result."""
        deferred = self._runtime.submit(specification, owner)
        self._runtime.schedule(deferred.ref, owner, workload)
        return deferred

    def wait_terminal(
        self,
        ref: JobRef,
        actor: OwnerRef,
        *,
        timeout: float = 300.0,
    ) -> TerminalOutcome:
        """Block until the job is terminal, mapping state to an OperationOutcome."""
        deadline = time.monotonic() + max(0.0, timeout)
        snapshot = self._runtime.get(ref, actor)
        while not snapshot.is_terminal:
            if time.monotonic() >= deadline:
                raise TaskWaitTimeout(snapshot, timeout)
            time.sleep(0.01)
            snapshot = self._runtime.get(ref, actor)
        return self._to_outcome(snapshot)

    def snapshot(self, ref: JobRef, actor: OwnerRef) -> JobSnapshot:
        return self._runtime.get(ref, actor)

    def to_operation_result(self, snapshot: JobSnapshot) -> OperationResult[JobSnapshot]:
        """Shared serialization schema: OperationResult carrying the snapshot.

        The operation contract forbids values on failed/cancelled results, so
        the snapshot is carried only on completed results; the immutable
        :class:`TerminalOutcome` remains the full terminal record.
        """
        outcome = self._to_outcome(snapshot).outcome
        terminal = self._to_outcome(snapshot)
        counts = OperationCounts(
            succeeded=1 if outcome is OperationOutcome.COMPLETED else 0,
            failed=1 if outcome is OperationOutcome.FAILED else 0,
            cancelled=1 if outcome is OperationOutcome.CANCELLED else 0,
        )
        value = snapshot if outcome is OperationOutcome.COMPLETED else None
        return OperationResult(
            outcome, value, diagnostics=terminal.diagnostics, counts=counts, run_id=snapshot.ref.run_id
        )

    @staticmethod
    def _to_outcome(snapshot: JobSnapshot) -> TerminalOutcome:
        if snapshot.state is JobState.COMPLETED:
            return TerminalOutcome(snapshot, OperationOutcome.COMPLETED)
        if snapshot.state is JobState.FAILED:
            return TerminalOutcome(
                snapshot,
                OperationOutcome.FAILED,
                (Diagnostic("JOB_FAILED", "The background job failed.", category=ErrorCategory.INTERNAL),),
            )
        if snapshot.state in TERMINAL_STATES:
            return TerminalOutcome(
                snapshot,
                OperationOutcome.CANCELLED,
                (Diagnostic("JOB_CANCELLED", "The background job was cancelled.", category=ErrorCategory.CANCELLED),),
            )
        return TerminalOutcome(snapshot, OperationOutcome.CANCELLED, ())


class SessionJobGate:
    """Accepts completion events for the active session; audits stale ones."""

    def __init__(self, runtime: TaskRuntime) -> None:
        self._runtime = runtime
        self._audited: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._subscription: Subscription | None = None
        self._active_session_id: str | None = None

    def activate(self, session_id: str | None) -> None:
        with self._lock:
            self._active_session_id = session_id

    def subscribe(self) -> None:
        with self._lock:
            if self._subscription is not None:
                return
            self._subscription = self._runtime.subscribe(
                self._on_event, event_filter=TaskEventFilter(event_types=frozenset({JobEventType.FINISHED}))
            )

    def close(self) -> None:
        with self._lock:
            if self._subscription is not None:
                self._subscription.close()
                self._subscription = None

    def accepts(self, event: JobEvent) -> bool:
        """True when the event belongs to the active session and is terminal."""
        with self._lock:
            active = self._active_session_id
        event_session = event.snapshot.owner.session_id
        if active is None or event_session != active:
            return False
        return event.snapshot.state in TERMINAL_STATES

    def accept_event(self, event: JobEvent) -> tuple[bool, bool]:
        """Returns (accepted, audited). Stale-session events are audit-only."""
        if self.accepts(event):
            return True, False
        self._record_audit(event)
        return False, True

    def audited(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._audited)

    def _on_event(self, event: JobEvent) -> None:
        if not self.accepts(event):
            self._record_audit(event)

    def _record_audit(self, event: JobEvent) -> None:
        with self._lock:
            self._audited.append({
                "run_id": event.snapshot.ref.run_id,
                "session_id": event.snapshot.owner.session_id,
                "state": event.snapshot.state.value,
                "code": event.code,
                "message": event.message,
                "occurred_at": event.occurred_at.isoformat(),
            })
