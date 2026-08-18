"""Thread-safe task state authority with owner-scoped controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
import secrets
import threading
import time
from typing import Protocol

from transbridge.application.contracts import Deferred, JobRef

from .backends import TaskBackend, ThreadBackend
from .controls import (
    CancellationToken,
    CommitPermit,
    CommitResult,
    ControlProjection,
    ShutdownPolicy,
    ShutdownResult,
    StopPolicy,
    StopResult,
    TaskCancelled,
)
from .events import JobEvent, JobEventType, Subscription, TaskEventFilter
from .models import (
    TERMINAL_STATES,
    JobSnapshot,
    JobSpec,
    JobState,
    OwnerRef,
    TaskAccessError,
    TransitionError,
)

logger = logging.getLogger(__name__)


def _validate_progress(progress: dict[str, object]) -> dict[str, object]:
    if not isinstance(progress, dict):
        raise TypeError("progress must be a flat dictionary")
    normalized: dict[str, object] = {}
    for key, value in progress.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("progress keys must be non-empty strings")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise TypeError("progress values must be JSON-safe scalars")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("progress numbers must be finite")
        normalized[key] = value
    return normalized


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _IdGenerator(Protocol):
    def new_id(self) -> str: ...


@dataclass(slots=True)
class _JobRecord:
    ref: JobRef
    owner: OwnerRef
    specification: JobSpec
    state: JobState
    revision: int
    sequence: int
    created_at: datetime
    updated_at: datetime
    cancellation: CancellationToken
    backend: TaskBackend | None = None
    checkpoint_requested: bool = False
    progress: dict[str, object] = field(default_factory=dict)


_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({JobState.PAUSED, JobState.CANCELLING, JobState.COMPLETED, JobState.FAILED}),
    JobState.PAUSED: frozenset({JobState.RUNNING, JobState.CANCELLING, JobState.COMPLETED, JobState.FAILED}),
    JobState.CANCELLING: frozenset({JobState.CANCELLED}),
    JobState.CANCELLED: frozenset(),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
}


class TaskRuntime:
    """Owns task state; execution backends may only request legal transitions."""

    MANAGE_PERMISSION = "tasks:manage"

    def __init__(
        self,
        *,
        id_generator: _IdGenerator,
        clock: _Clock,
        backend: TaskBackend | None = None,
    ) -> None:
        self._id_generator = id_generator
        self._clock = clock
        self._lock = threading.RLock()
        self._shutdown_lock = threading.Lock()
        self._state_changed = threading.Condition(self._lock)
        self._jobs: dict[str, _JobRecord] = {}
        self._commit_permits: dict[str, tuple[str, OwnerRef, int]] = {}
        self._subscriptions: dict[int, tuple[TaskEventFilter, Callable[[JobEvent], None]]] = {}
        self._next_subscription = 0
        self._closed = False
        self._backend = backend or ThreadBackend()
        self._shutdown_result: ShutdownResult | None = None

    def submit(self, specification: JobSpec, owner: OwnerRef) -> Deferred[JobRef]:
        with self._lock:
            if self._closed:
                raise RuntimeError("task runtime is closed")
            run_id = self._id_generator.new_id()
            if not run_id or not run_id.strip():
                raise ValueError("id generator returned an empty run_id")
            if run_id in self._jobs:
                raise ValueError("id generator returned a duplicate run_id")
            now = self._clock.now()
            ref = JobRef(job_id=run_id, owner_id=owner.owner_id, run_id=run_id)
            record = _JobRecord(
                ref=ref,
                owner=owner,
                specification=specification,
                state=JobState.QUEUED,
                revision=0,
                sequence=1,
                created_at=now,
                updated_at=now,
                cancellation=CancellationToken(),
            )
            self._jobs[run_id] = record
            event = self._event(record, JobEventType.CREATED, previous_state=None)
        self._publish(event)
        return Deferred(ref)

    def get(self, ref: JobRef, actor: OwnerRef) -> JobSnapshot:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            return self._snapshot(record)

    def update_progress(
        self,
        ref: JobRef,
        actor: OwnerRef,
        progress: dict[str, object],
    ) -> JobSnapshot:
        """Merge a JSON-safe flat progress update into the authoritative snapshot."""
        normalized = _validate_progress(progress)
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            if record.state in TERMINAL_STATES:
                raise TransitionError(
                    "terminal_state",
                    "cannot update progress for a terminal task",
                    ref=record.ref,
                    current=record.state,
                    target=record.state,
                )
            record.progress.update(normalized)
            record.revision += 1
            record.sequence += 1
            record.updated_at = self._clock.now()
            event = self._event(record, JobEventType.PROGRESS, previous_state=record.state)
            snapshot = event.snapshot
        self._publish(event)
        return snapshot

    def list(self, actor: OwnerRef) -> tuple[JobSnapshot, ...]:
        with self._lock:
            records = tuple(self._jobs.values())
            if self.MANAGE_PERMISSION in actor.permissions:
                return tuple(self._snapshot(record) for record in records)
            return tuple(self._snapshot(record) for record in records if record.owner.same_scope(actor))

    def start(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        return self._transition(ref, actor, JobState.RUNNING, expected_revision)

    def pause(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            if not record.specification.capabilities.supports_pause:
                self._unsupported(record, JobState.PAUSED, "pause")
        return self._transition(ref, actor, JobState.PAUSED, expected_revision)

    def resume(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            if not record.specification.capabilities.supports_resume:
                self._unsupported(record, JobState.RUNNING, "resume")
        return self._transition(ref, actor, JobState.RUNNING, expected_revision)

    def cancel(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        snapshot, record, event = self._cancel_transition(
            ref,
            actor,
            expected_revision=expected_revision,
            reason="cancel requested",
            checkpoint_requested=False,
        )
        self._publish(event)
        self._signal_backend_cancellation(record)
        return snapshot

    def stop(
        self,
        ref: JobRef,
        actor: OwnerRef,
        *,
        policy: StopPolicy = StopPolicy.PRESERVE_CHECKPOINT,
        expected_revision: int | None = None,
    ) -> StopResult:
        """Business-level stop, distinct from queue-wide shutdown."""

        checkpoint_requested = policy is StopPolicy.PRESERVE_CHECKPOINT
        snapshot, record, event = self._cancel_transition(
            ref,
            actor,
            expected_revision=expected_revision,
            reason=f"stop requested ({policy.value})",
            checkpoint_requested=checkpoint_requested,
        )
        self._publish(event)
        self._signal_backend_cancellation(record)
        return StopResult(snapshot, policy, record.checkpoint_requested)

    def complete(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        return self._transition(ref, actor, JobState.COMPLETED, expected_revision)

    def fail(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        return self._transition(ref, actor, JobState.FAILED, expected_revision)

    def finish_cancelled(self, ref: JobRef, actor: OwnerRef, *, expected_revision: int | None = None) -> JobSnapshot:
        return self._transition(ref, actor, JobState.CANCELLED, expected_revision)

    def cancellation_token(self, ref: JobRef, actor: OwnerRef) -> CancellationToken:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            return record.cancellation

    def commit_permit(self, ref: JobRef, actor: OwnerRef) -> CommitPermit:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            if record.state is not JobState.RUNNING or record.cancellation.is_cancelled:
                raise TransitionError(
                    "commit_not_allowed",
                    f"cannot issue a commit permit while task is {record.state.value}",
                    ref=record.ref,
                    current=record.state,
                    target=JobState.COMPLETED,
                )
            nonce = secrets.token_urlsafe(32)
            self._commit_permits[nonce] = (record.ref.run_id, record.owner, record.revision)
            return CommitPermit(record.ref.run_id, record.owner, record.revision, nonce)

    def try_commit(self, permit: CommitPermit, mutation: Callable[[], None]) -> CommitResult:
        """Run a formal mutation under the same lock as cancellation arbitration.

        A successful mutation advances the revision, making the permit one-shot. A
        rejected late/foreign permit produces an immutable ``ignored`` event.
        """

        mutation_error: BaseException | None = None
        with self._lock:
            record = self._jobs.get(permit.run_id)
            reason: str | None = None
            if record is None or record.ref.run_id != permit.run_id:
                raise TaskAccessError("job_not_found", "commit permit references an unknown task")
            registered = self._commit_permits.pop(permit.nonce, None)
            if not record.owner.same_scope(permit.owner):
                reason = "owner_mismatch"
            elif record.state is not JobState.RUNNING:
                reason = "terminal_or_inactive"
            elif record.cancellation.is_cancelled:
                reason = "cancelled"
            elif record.revision != permit.revision:
                reason = "revision_conflict"
            elif registered != (permit.run_id, permit.owner, permit.revision):
                reason = "permit_unknown_or_consumed"

            if reason is not None:
                event = self._diagnostic_event(
                    record,
                    JobEventType.IGNORED,
                    code=f"ignored_commit_{reason}",
                    message="late or stale workload commit was ignored",
                )
                result = CommitResult(False, event.snapshot, reason)
            else:
                try:
                    mutation()
                except BaseException as exc:  # noqa: BLE001 - consume permit and fail closed
                    previous = record.state
                    record.state = JobState.FAILED
                    record.revision += 1
                    record.sequence += 1
                    record.updated_at = self._clock.now()
                    self._discard_permits(record.ref.run_id)
                    self._state_changed.notify_all()
                    event = JobEvent(
                        event_type=JobEventType.FINISHED,
                        snapshot=self._snapshot(record),
                        sequence=record.sequence,
                        revision=record.revision,
                        occurred_at=record.updated_at,
                        previous_state=previous,
                        code="commit_mutation_failed",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                    mutation_error = exc
                else:
                    record.revision += 1
                    record.sequence += 1
                    record.updated_at = self._clock.now()
                    self._discard_permits(record.ref.run_id)
                    event = JobEvent(
                        event_type=JobEventType.DIAGNOSTIC,
                        snapshot=self._snapshot(record),
                        sequence=record.sequence,
                        revision=record.revision,
                        occurred_at=record.updated_at,
                        code="commit_accepted",
                        message="workload mutation committed",
                    )
                    result = CommitResult(True, event.snapshot)
        self._publish(event)
        if mutation_error is not None:
            raise mutation_error
        return result

    def schedule(
        self,
        ref: JobRef,
        actor: OwnerRef,
        workload: Callable[[CancellationToken], object],
        *,
        backend: TaskBackend | None = None,
    ) -> JobSnapshot:
        """Start a workload while keeping terminal state ownership in the runtime."""

        selected = backend or self._backend
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            record.backend = selected
        snapshot = self.start(ref, actor)

        def execute() -> None:
            try:
                workload(record.cancellation)
            except TaskCancelled:
                self._finish_after_workload(record, cancelled=True)
            except BaseException as exc:  # noqa: BLE001 - backend exception becomes task state
                self._finish_after_workload(record, error=exc)
            else:
                self._finish_after_workload(record, cancelled=record.cancellation.is_cancelled)

        try:
            selected.start(ref.run_id, execute)
        except BaseException as exc:  # noqa: BLE001 - scheduling failure is a failed task
            self._record_diagnostic(record, "backend_start_failed", str(exc))
            try:
                self.fail(ref, record.owner)
            except TransitionError:
                pass
            raise
        return snapshot

    def controls(self, ref: JobRef, actor: OwnerRef) -> ControlProjection:
        snapshot = self.get(ref, actor)
        capabilities = snapshot.specification.capabilities
        active = not snapshot.is_terminal
        return ControlProjection(
            pause_visible=capabilities.supports_pause,
            pause_enabled=capabilities.supports_pause and snapshot.state is JobState.RUNNING,
            resume_visible=capabilities.supports_resume,
            resume_enabled=capabilities.supports_resume and snapshot.state is JobState.PAUSED,
            cancel_visible=capabilities.supports_cancel,
            cancel_enabled=capabilities.supports_cancel
            and snapshot.state in {JobState.QUEUED, JobState.RUNNING, JobState.PAUSED},
            stop_visible=capabilities.supports_cancel,
            stop_enabled=capabilities.supports_cancel and active,
        )

    def subscribe(
        self,
        callback: Callable[[JobEvent], None],
        *,
        event_filter: TaskEventFilter | None = None,
    ) -> Subscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("task runtime is closed")
            self._next_subscription += 1
            token = self._next_subscription
            self._subscriptions[token] = (event_filter or TaskEventFilter(), callback)
        return Subscription(token, self._remove_subscription)

    def shutdown(
        self,
        *,
        grace: float = 5.0,
        policy: ShutdownPolicy = ShutdownPolicy.CHECKPOINT_AND_CANCEL,
    ) -> ShutdownResult:
        """Close admission, apply policy, and report resources that really stopped."""

        if grace < 0:
            raise ValueError("shutdown grace must not be negative")
        with self._shutdown_lock:
            return self._shutdown_once(grace=grace, policy=policy)

    def _shutdown_once(self, *, grace: float, policy: ShutdownPolicy) -> ShutdownResult:
        with self._lock:
            if self._shutdown_result is not None:
                return self._shutdown_result
            self._closed = True
            records = tuple(record for record in self._jobs.values() if record.state not in TERMINAL_STATES)

        if policy is not ShutdownPolicy.WAIT:
            stop_policy = (
                StopPolicy.PRESERVE_CHECKPOINT
                if policy is ShutdownPolicy.CHECKPOINT_AND_CANCEL
                else StopPolicy.DISCARD_CHECKPOINT
            )
            for record in records:
                try:
                    self.stop(record.ref, record.owner, policy=stop_policy)
                except TransitionError:
                    pass

        deadline = time.monotonic() + grace
        joined: list = []
        timed_out: list = []
        backends: list[TaskBackend] = []
        for record in records:
            backend = record.backend
            if backend is not None and all(backend is not item for item in backends):
                backends.append(backend)
            remaining = max(0.0, deadline - time.monotonic())
            if backend is None:
                with self._state_changed:
                    while record.state not in TERMINAL_STATES and remaining > 0:
                        self._state_changed.wait(remaining)
                        remaining = max(0.0, deadline - time.monotonic())
                    done = record.state in TERMINAL_STATES
            else:
                try:
                    done = backend.join(record.ref.run_id, remaining)
                except BaseException as exc:  # noqa: BLE001 - continue releasing other backends
                    done = False
                    self._record_diagnostic(record, "backend_join_failed", str(exc))
            if done:
                joined.append(record.ref)
                with self._lock:
                    current = record.state
                if current is JobState.CANCELLING:
                    try:
                        self.finish_cancelled(record.ref, record.owner)
                    except TransitionError:
                        pass
            else:
                timed_out.append(record.ref)
                self._record_diagnostic(
                    record,
                    "shutdown_timeout",
                    "shutdown grace expired; backend resource may still be active",
                )

        if all(self._backend is not item for item in backends):
            backends.append(self._backend)
        released = not timed_out
        for backend in backends:
            try:
                released = backend.close(max(0.0, deadline - time.monotonic())) and released
            except BaseException as exc:  # noqa: BLE001 - report incomplete release
                released = False
                logger.exception("Task backend close failed: %s", exc)

        with self._lock:
            self._subscriptions.clear()
            self._shutdown_result = ShutdownResult(
                policy=policy,
                admission_closed=True,
                requested=tuple(record.ref for record in records),
                joined=tuple(joined),
                timed_out=tuple(timed_out),
                backend_released=released,
            )
            return self._shutdown_result

    def close(self) -> None:
        self.shutdown(grace=0)

    def _transition(
        self,
        ref: JobRef,
        actor: OwnerRef,
        target: JobState,
        expected_revision: int | None,
    ) -> JobSnapshot:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            if expected_revision is not None and record.revision != expected_revision:
                raise TransitionError(
                    "revision_conflict",
                    f"expected revision {expected_revision}, found {record.revision}",
                    ref=record.ref,
                    current=record.state,
                    target=target,
                )
            if target not in _TRANSITIONS[record.state]:
                code = "terminal_state" if record.state in TERMINAL_STATES else "invalid_transition"
                raise TransitionError(
                    code,
                    f"cannot transition {record.state.value} to {target.value}",
                    ref=record.ref,
                    current=record.state,
                    target=target,
                )
            previous = record.state
            record.state = target
            record.revision += 1
            record.sequence += 1
            record.updated_at = self._clock.now()
            self._discard_permits(record.ref.run_id)
            self._state_changed.notify_all()
            event_type = JobEventType.FINISHED if target in TERMINAL_STATES else JobEventType.STATE_CHANGED
            event = self._event(record, event_type, previous_state=previous)
            snapshot = event.snapshot
        self._publish(event)
        return snapshot

    def _resolve(self, ref: JobRef) -> _JobRecord:
        record = self._jobs.get(ref.job_id)
        if record is None or record.ref != ref:
            raise TaskAccessError("job_not_found", "task reference is unknown")
        return record

    def _authorize(self, record: _JobRecord, actor: OwnerRef) -> None:
        if self.MANAGE_PERMISSION in actor.permissions or record.owner.same_scope(actor):
            return
        raise TaskAccessError("owner_mismatch", "task owner scope does not match")

    def _unsupported(self, record: _JobRecord, target: JobState, action: str) -> None:
        raise TransitionError(
            "unsupported_control",
            f"task does not support {action}",
            ref=record.ref,
            current=record.state,
            target=target,
        )

    def _snapshot(self, record: _JobRecord) -> JobSnapshot:
        return JobSnapshot(
            ref=record.ref,
            owner=record.owner,
            specification=record.specification,
            state=record.state,
            revision=record.revision,
            sequence=record.sequence,
            created_at=record.created_at,
            updated_at=record.updated_at,
            progress=tuple(sorted(record.progress.items())),
        )

    def _event(
        self,
        record: _JobRecord,
        event_type: JobEventType,
        *,
        previous_state: JobState | None,
    ) -> JobEvent:
        snapshot = self._snapshot(record)
        return JobEvent(
            event_type=event_type,
            snapshot=snapshot,
            sequence=record.sequence,
            revision=record.revision,
            occurred_at=record.updated_at,
            previous_state=previous_state,
        )

    def _cancel_transition(
        self,
        ref: JobRef,
        actor: OwnerRef,
        *,
        expected_revision: int | None,
        reason: str,
        checkpoint_requested: bool,
    ) -> tuple[JobSnapshot, _JobRecord, JobEvent]:
        with self._lock:
            record = self._resolve(ref)
            self._authorize(record, actor)
            if not record.specification.capabilities.supports_cancel:
                self._unsupported(record, JobState.CANCELLING, "cancel")
            if expected_revision is not None and record.revision != expected_revision:
                raise TransitionError(
                    "revision_conflict",
                    f"expected revision {expected_revision}, found {record.revision}",
                    ref=record.ref,
                    current=record.state,
                    target=JobState.CANCELLING,
                )
            target = JobState.CANCELLED if record.state is JobState.QUEUED else JobState.CANCELLING
            if target not in _TRANSITIONS[record.state]:
                code = "terminal_state" if record.state in TERMINAL_STATES else "invalid_transition"
                raise TransitionError(
                    code,
                    f"cannot transition {record.state.value} to {target.value}",
                    ref=record.ref,
                    current=record.state,
                    target=target,
                )
            previous = record.state
            record.state = target
            record.revision += 1
            record.sequence += 1
            record.updated_at = self._clock.now()
            record.checkpoint_requested = checkpoint_requested and record.specification.capabilities.supports_checkpoint
            record.cancellation._cancel(reason)
            self._discard_permits(record.ref.run_id)
            self._state_changed.notify_all()
            event_type = JobEventType.FINISHED if target in TERMINAL_STATES else JobEventType.STATE_CHANGED
            event = self._event(record, event_type, previous_state=previous)
            return event.snapshot, record, event

    def _signal_backend_cancellation(self, record: _JobRecord) -> None:
        backend = record.backend
        if backend is None:
            return
        try:
            backend.cancel_hint(record.ref.run_id)
            if backend.join(record.ref.run_id, 0):
                with self._lock:
                    cancelling = record.state is JobState.CANCELLING
                if cancelling:
                    self.finish_cancelled(record.ref, record.owner)
        except BaseException as exc:  # noqa: BLE001 - cancellation remains cooperative
            self._record_diagnostic(record, "backend_cancel_hint_failed", str(exc))

    def _discard_permits(self, run_id: str) -> None:
        stale = [nonce for nonce, registered in self._commit_permits.items() if registered[0] == run_id]
        for nonce in stale:
            self._commit_permits.pop(nonce, None)

    def _finish_after_workload(
        self,
        record: _JobRecord,
        *,
        cancelled: bool = False,
        error: BaseException | None = None,
    ) -> None:
        with self._lock:
            state = record.state
        if state in TERMINAL_STATES:
            return
        if cancelled or state is JobState.CANCELLING:
            try:
                if state in {JobState.RUNNING, JobState.PAUSED}:
                    self.cancel(record.ref, record.owner)
                with self._lock:
                    state = record.state
                if state is JobState.CANCELLING:
                    self.finish_cancelled(record.ref, record.owner)
            except TransitionError:
                pass
            return
        if error is not None:
            self._record_diagnostic(record, "backend_workload_failed", str(error))
            try:
                self.fail(record.ref, record.owner)
            except TransitionError:
                pass
            return
        try:
            self.complete(record.ref, record.owner)
        except TransitionError:
            self._record_diagnostic(
                record,
                "ignored_late_completion",
                "workload completion lost a terminal-state race",
                event_type=JobEventType.IGNORED,
            )

    def _diagnostic_event(
        self,
        record: _JobRecord,
        event_type: JobEventType,
        *,
        code: str,
        message: str,
    ) -> JobEvent:
        record.sequence += 1
        record.updated_at = self._clock.now()
        return JobEvent(
            event_type=event_type,
            snapshot=self._snapshot(record),
            sequence=record.sequence,
            revision=record.revision,
            occurred_at=record.updated_at,
            code=code,
            message=message,
        )

    def _record_diagnostic(
        self,
        record: _JobRecord,
        code: str,
        message: str,
        *,
        event_type: JobEventType = JobEventType.DIAGNOSTIC,
    ) -> None:
        with self._lock:
            event = self._diagnostic_event(record, event_type, code=code, message=message)
        self._publish(event)

    def _publish(self, event: JobEvent) -> None:
        with self._lock:
            callbacks = tuple(
                callback for event_filter, callback in self._subscriptions.values() if event_filter.matches(event)
            )
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                logger.exception("Task event callback failed")

    def _remove_subscription(self, token: int) -> None:
        with self._lock:
            self._subscriptions.pop(token, None)
