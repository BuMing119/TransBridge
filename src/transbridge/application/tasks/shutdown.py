"""Reversible admission suspension and backend draining before final shutdown."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import threading
import time
from typing import Protocol

from transbridge.application.contracts import JobRef

from .backends import TaskBackend
from .controls import ShutdownPolicy, ShutdownResult, StopPolicy
from .models import TERMINAL_STATES, JobSpec, JobState, OwnerRef, TransitionError

logger = logging.getLogger(__name__)


class ShutdownRecord(Protocol):
    """Runtime-owned state; this coordinator only reads it under the shared condition."""

    ref: JobRef
    owner: OwnerRef
    specification: JobSpec
    state: JobState
    backend: TaskBackend | None
    dispatching: bool
    cleanup_error: str | None


@dataclass(frozen=True, slots=True)
class DrainStatus:
    pending: tuple[JobRef, ...]
    errors: tuple[str, ...]


class TaskDrain:
    """A close attempt holds admission until saved, or releases it on abort."""

    def __init__(self, coordinator: TaskShutdown, token: object, records: tuple[ShutdownRecord, ...]) -> None:
        self._coordinator = coordinator
        self._token = token
        self._records = records
        self._errors: list[str] = []

    def cancel(self) -> None:
        self._errors.extend(self._coordinator.cancel(self._records, StopPolicy.PRESERVE_CHECKPOINT))

    def poll(self) -> DrainStatus:
        pending = []
        errors = list(self._errors)
        for record in self._records:
            try:
                if not self._coordinator.wait(record, 0):
                    pending.append(record.ref)
            except Exception as exc:
                errors.append(f"{record.ref.run_id}: 无法确认后台结束：{exc}")
            with self._coordinator.condition:
                if record.cleanup_error:
                    errors.append(f"{record.ref.run_id}: {record.cleanup_error}")
        return DrainStatus(tuple(pending), tuple(dict.fromkeys(errors)))

    def close(self) -> None:
        self._coordinator.release(self._token)


class TaskShutdown:
    """Own admission and resource lifetime; task transitions remain in TaskRuntime."""

    def __init__(
        self,
        *,
        condition: threading.Condition,
        records: Callable[[], tuple[ShutdownRecord, ...]],
        backend: TaskBackend,
        stop: Callable,
        finish_cancelled: Callable,
        diagnostic: Callable,
        clear_subscriptions: Callable[[], None],
    ) -> None:
        self.condition = condition
        self._records = records
        self._backend = backend
        self._stop = stop
        self._finish_cancelled = finish_cancelled
        self._diagnostic = diagnostic
        self._clear_subscriptions = clear_subscriptions
        self._holds: set[object] = set()
        self.closed = False
        self._shutdown_lock = threading.Lock()
        self._result: ShutdownResult | None = None

    def check_admission(self) -> None:
        # Called under the same lock that registers a new task.
        if self.closed:
            raise RuntimeError("task runtime is closed")
        if self._holds:
            raise RuntimeError("task admission is suspended while the application is closing")

    def prepare_close(self) -> TaskDrain:
        with self.condition:
            if self.closed:
                raise RuntimeError("task runtime is closed")
            token = object()
            self._holds.add(token)
            return TaskDrain(self, token, self._records())

    def release(self, token: object) -> None:
        with self.condition:
            self._holds.discard(token)

    def cancel(self, records: tuple[ShutdownRecord, ...], policy: StopPolicy) -> tuple[str, ...]:
        errors = []
        for record in records:
            with self.condition:
                if record.state in TERMINAL_STATES or record.state is JobState.CANCELLING:
                    continue
            try:
                self._stop(record.ref, record.owner, policy=policy)
            except TransitionError as exc:
                with self.condition:
                    if record.state not in TERMINAL_STATES and record.state is not JobState.CANCELLING:
                        errors.append(f"{record.specification.display_name or record.ref.run_id}: 无法取消：{exc}")
        return tuple(errors)

    def wait(self, record: ShutdownRecord, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.condition:
            while record.dispatching:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            backend = record.backend
        if backend is not None:
            if not backend.join(record.ref.run_id, max(0.0, deadline - time.monotonic())):
                return False
            with self.condition:
                cancelling = record.state is JobState.CANCELLING
            if cancelling:
                try:
                    self._finish_cancelled(record.ref, record.owner)
                except TransitionError:
                    pass  # The workload won the terminal transition race.
        with self.condition:
            while record.state not in TERMINAL_STATES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def shutdown(self, *, grace: float, policy: ShutdownPolicy) -> ShutdownResult:
        if grace < 0:
            raise ValueError("shutdown grace must not be negative")
        with self._shutdown_lock:
            if self._result is not None:
                return self._result
            return self._shutdown_once(grace, policy)

    def _shutdown_once(self, grace: float, policy: ShutdownPolicy) -> ShutdownResult:
        with self.condition:
            self.closed = True
            # Terminal jobs may still be rolling back or releasing their backend.
            candidates = self._records()
            records = [record for record in candidates if record.state not in TERMINAL_STATES or record.dispatching]
            active_ids = {record.ref.run_id for record in records}
        for record in candidates:
            if record.ref.run_id in active_ids:
                continue
            try:
                if not self.wait(record, 0):
                    records.append(record)
            except BaseException:  # noqa: BLE001 - retry below and retain the join diagnostic
                records.append(record)
        records = tuple(records)
        if policy is not ShutdownPolicy.WAIT:
            stop_policy = (
                StopPolicy.PRESERVE_CHECKPOINT
                if policy is ShutdownPolicy.CHECKPOINT_AND_CANCEL
                else StopPolicy.DISCARD_CHECKPOINT
            )
            self.cancel(records, stop_policy)
        deadline = time.monotonic() + grace
        joined, timed_out = [], []
        backends: list[TaskBackend] = []
        for record in candidates:
            if record.backend is not None and all(record.backend is not item for item in backends):
                backends.append(record.backend)
        for record in records:
            try:
                done = self.wait(record, max(0.0, deadline - time.monotonic()))
            except BaseException as exc:  # noqa: BLE001 - continue releasing other backends
                done = False
                self._diagnostic(record, "backend_join_failed", str(exc))
            if done:
                joined.append(record.ref)
            else:
                timed_out.append(record.ref)
                self._diagnostic(record, "shutdown_timeout", "shutdown grace expired; backend may still be active")
        if all(self._backend is not item for item in backends):
            backends.append(self._backend)
        released = not timed_out
        for backend in backends:
            try:
                released = backend.close(max(0.0, deadline - time.monotonic())) and released
            except BaseException as exc:  # noqa: BLE001 - report incomplete release
                released = False
                logger.exception("Task backend close failed: %s", exc)
        with self.condition:
            self._clear_subscriptions()
            self._result = ShutdownResult(
                policy, True, tuple(r.ref for r in records), tuple(joined), tuple(timed_out), released
            )
            return self._result
