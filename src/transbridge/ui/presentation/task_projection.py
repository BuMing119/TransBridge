"""Qt-free TaskRuntime event projection and subscription lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading

from transbridge.application.tasks.activity import (
    TaskActivityEvidence,
    TaskActivityEvidencePort,
    TaskActivityViewState,
    TaskDiagnosticRef,
    TaskOwnerScope,
    UnsupportedTaskActivityEvidence,
    activity_from_snapshot,
)
from transbridge.application.tasks.events import JobEvent, JobEventType, Subscription, TaskEventFilter
from transbridge.application.tasks.models import TERMINAL_STATES, JobSnapshot, OwnerRef
from transbridge.application.tasks.runtime import TaskRuntime


@dataclass(frozen=True, slots=True)
class TaskProjectionReduction:
    state: TaskActivityViewState
    accepted: bool
    reason: str = ""


class TaskProjectionReducer:
    """Rejects duplicate, out-of-order, foreign and terminal-reversing events."""

    def __init__(self, *, max_diagnostics: int = 20) -> None:
        if max_diagnostics <= 0:
            raise ValueError("max_diagnostics must be positive")
        self._max_diagnostics = max_diagnostics

    def reduce(
        self,
        previous: TaskActivityViewState | None,
        event: JobEvent,
        *,
        evidence: TaskActivityEvidence | None = None,
    ) -> TaskProjectionReduction:
        snapshot = event.snapshot
        run_id = snapshot.ref.run_id or snapshot.ref.job_id
        if event.sequence != snapshot.sequence or event.revision != snapshot.revision:
            if previous is None:
                seed = activity_from_snapshot(snapshot, evidence=evidence)
                return TaskProjectionReduction(seed, False, "event_snapshot_mismatch")
            return TaskProjectionReduction(previous, False, "event_snapshot_mismatch")
        if previous is not None:
            if previous.run_id != run_id or previous.owner != TaskOwnerScope.from_owner(snapshot.owner):
                return TaskProjectionReduction(previous, False, "identity_mismatch")
            if event.sequence < previous.sequence or event.revision < previous.revision:
                return TaskProjectionReduction(previous, False, "out_of_order")
            if event.sequence == previous.sequence:
                return TaskProjectionReduction(previous, False, "duplicate")
            if previous.state in TERMINAL_STATES and snapshot.state is not previous.state:
                return TaskProjectionReduction(previous, False, "terminal_state")

        diagnostics = previous.diagnostic_refs if previous is not None else ()
        if event.code:
            diagnostics = (*diagnostics, TaskDiagnosticRef(event.code, event.sequence))
            diagnostics = diagnostics[-self._max_diagnostics :]
        state = activity_from_snapshot(
            snapshot,
            evidence=evidence,
            diagnostic_refs=diagnostics,
        )
        return TaskProjectionReduction(state, True)


class TaskProjectionBinding:
    """Subscribes once, seeds once, and never polls ``TaskRuntime.list``."""

    def __init__(
        self,
        runtime: TaskRuntime,
        actor: OwnerRef,
        on_change: Callable[[TaskActivityViewState], None],
        *,
        evidence: TaskActivityEvidencePort | None = None,
        reducer: TaskProjectionReducer | None = None,
    ) -> None:
        self._runtime = runtime
        self._actor = actor
        self._on_change = on_change
        self._evidence = evidence or UnsupportedTaskActivityEvidence()
        self._reducer = reducer or TaskProjectionReducer()
        self._states: dict[str, TaskActivityViewState] = {}
        self._subscription: Subscription | None = None
        self._started = False
        self._closed = False
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def start(self) -> None:
        with self._lock:
            if self._closed or self._started:
                return
            self._started = True
        event_filter = None
        if TaskRuntime.MANAGE_PERMISSION not in self._actor.permissions:
            # TaskEventFilter only narrows owner_id; _accepts_owner below still
            # verifies the complete owner scope before projecting the event.
            event_filter = TaskEventFilter(owner_id=self._actor.owner_id)
        try:
            subscription = self._runtime.subscribe(self._on_event, event_filter=event_filter)
            with self._lock:
                if self._closed:
                    subscription.close()
                    return
                self._subscription = subscription
            for snapshot in self._runtime.list(self._actor):
                self._on_event(_seed_event(snapshot))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()

    def states(self) -> tuple[TaskActivityViewState, ...]:
        with self._lock:
            return tuple(sorted(self._states.values(), key=lambda state: (state.sequence, state.run_id)))

    def _on_event(self, event: JobEvent) -> None:
        if not self._accepts_owner(event.snapshot.owner):
            return
        supplied = self._evidence.for_snapshot(event.snapshot, self._actor)
        run_id = event.snapshot.ref.run_id or event.snapshot.ref.job_id
        with self._lock:
            if self._closed:
                return
            reduction = self._reducer.reduce(self._states.get(run_id), event, evidence=supplied)
            if not reduction.accepted:
                return
            self._states[run_id] = reduction.state
        self._on_change(reduction.state)

    def _accepts_owner(self, owner: OwnerRef) -> bool:
        return TaskRuntime.MANAGE_PERMISSION in self._actor.permissions or owner.same_scope(self._actor)


def _seed_event(snapshot: JobSnapshot) -> JobEvent:
    return JobEvent(
        event_type=JobEventType.CREATED if snapshot.sequence == 1 else JobEventType.STATE_CHANGED,
        snapshot=snapshot,
        sequence=snapshot.sequence,
        revision=snapshot.revision,
        occurred_at=snapshot.updated_at,
    )
