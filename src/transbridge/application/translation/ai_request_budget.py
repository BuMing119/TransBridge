"""Run-scoped admission control for outbound AI requests.

The budget is deliberately independent from Qt and provider SDKs.  A lease
covers one logical ``chat``/``chat_stream`` invocation, including any retries
performed internally by the provider client.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
import time
from typing import Protocol


class EventState(Protocol):
    """Minimal event surface required by :class:`AiRequestBudget`."""

    def is_set(self) -> bool: ...


class AiRequestCancelledError(RuntimeError):
    """Raised when a request is cancelled before provider admission."""


@dataclass(frozen=True, slots=True)
class AiRequestBudgetSnapshot:
    """Diagnostics for admitted logical calls and independent preparation work.

    ``in_flight`` counts admitted logical SDK calls, including lightweight
    finalization and SDK retries. It is not a count of individual HTTP requests.
    """

    in_flight: int
    waiting: int
    peak_in_flight: int
    preparing: int = 0
    waiting_preparation: int = 0
    peak_preparing: int = 0


@dataclass(eq=False, slots=True)
class _Waiter:
    lane: Hashable
    pause_event: EventState | None

    @property
    def ready(self) -> bool:
        return self.pause_event is None or self.pause_event.is_set()


@dataclass(slots=True)
class _AdmissionQueue:
    """Round robin across ready lanes, FIFO among ready requests in a lane.

    All operations are protected by the enclosing budget's condition. Paused
    lanes do not reserve capacity, so other sources can borrow every free slot.
    """

    limit: int
    active: int = 0
    peak: int = 0
    waiting: int = 0
    lanes: dict[Hashable, deque[_Waiter]] = field(default_factory=dict)
    rotation: deque[Hashable] = field(default_factory=deque)

    def enqueue(self, waiter: _Waiter) -> None:
        if waiter.lane not in self.lanes:
            self.lanes[waiter.lane] = deque()
            self.rotation.append(waiter.lane)
        self.lanes[waiter.lane].append(waiter)
        self.waiting += 1

    def next_ready(self) -> _Waiter | None:
        for lane in self.rotation:
            for waiter in self.lanes[lane]:
                if waiter.ready:
                    return waiter
        return None

    def admit(self, waiter: _Waiter) -> None:
        self.rotation.rotate(-self.rotation.index(waiter.lane) - 1)
        self.active += 1
        self.peak = max(self.peak, self.active)

    def remove(self, waiter: _Waiter) -> None:
        queue = self.lanes[waiter.lane]
        queue.remove(waiter)
        if not queue:
            del self.lanes[waiter.lane]
            self.rotation.remove(waiter.lane)
        self.waiting -= 1


class AiRequestLease:
    """A single, idempotently releasable request-budget lease."""

    def __init__(self, budget: AiRequestBudget, *, preparation: bool = False) -> None:
        self._budget = budget
        self._preparation = preparation
        self._released = False
        self._lock = threading.Lock()

    def __enter__(self) -> AiRequestLease:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.release()
        return False

    def release(self) -> None:
        """Return the slot once; repeated calls are harmless."""

        with self._lock:
            if self._released:
                return
            self._released = True
        self._budget._release(preparation=self._preparation)


class AiRequestBudget:
    """Limit the number of provider requests in flight for one AI run.

    ``pause_event`` follows the worker convention used by TransBridge:
    ``set`` means running and ``clear`` means paused.  Cancellation is checked
    while waiting and immediately before a lease is granted.
    """

    _STATE_POLL_SECONDS = 0.05

    def __init__(self, max_in_flight: int, *, max_preparing: int | None = None) -> None:
        if isinstance(max_in_flight, bool) or not isinstance(max_in_flight, int) or max_in_flight <= 0:
            raise ValueError("max_in_flight must be a positive integer")
        if max_preparing is None:
            max_preparing = min(4, max_in_flight)
        if isinstance(max_preparing, bool) or not isinstance(max_preparing, int) or max_preparing <= 0:
            raise ValueError("max_preparing must be a positive integer")
        self._requests = _AdmissionQueue(max_in_flight)
        self._preparation = _AdmissionQueue(max_preparing)
        self._condition = threading.Condition()
        self._lane: ContextVar[Hashable | None] = ContextVar("ai_request_lane", default=None)

    @property
    def max_in_flight(self) -> int:
        return self._requests.limit

    @property
    def max_preparing(self) -> int:
        return self._preparation.limit

    @contextmanager
    def lane(self, key: Hashable) -> Iterator[None]:
        """Scope admissions to a source/stage lane in the current context.

        Thread pools do not automatically propagate this context. Capture the
        key before submission and restore it inside the submitted callable, or
        give each client its own explicit ``lane_key``.
        """

        hash(key)
        token = self._lane.set(key)
        try:
            yield
        finally:
            self._lane.reset(token)

    def capture_lane(self) -> Hashable | None:
        return self._lane.get()

    def snapshot(self) -> AiRequestBudgetSnapshot:
        with self._condition:
            return AiRequestBudgetSnapshot(
                in_flight=self._requests.active,
                waiting=self._requests.waiting,
                peak_in_flight=self._requests.peak,
                preparing=self._preparation.active,
                waiting_preparation=self._preparation.waiting,
                peak_preparing=self._preparation.peak,
            )

    def acquire(
        self,
        *,
        cancel_event: EventState | None = None,
        pause_event: EventState | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        timeout: float | None = None,
        lane_key: Hashable | None = None,
    ) -> AiRequestLease:
        """Wait for admission and return a lease.

        Args:
            cancel_event: An event whose set state cancels this admission.
            pause_event: An event whose clear state prevents new admission.
            is_cancelled: Optional additional cancellation predicate.
            timeout: Maximum wait in seconds. ``None`` waits indefinitely.
            lane_key: Explicit source/stage key, or the current lane context.

        Raises:
            AiRequestCancelledError: Cancellation wins before admission.
            TimeoutError: The optional timeout expires before admission.
        """

        return self._acquire(
            preparation=False,
            cancel_event=cancel_event,
            pause_event=pause_event,
            is_cancelled=is_cancelled,
            timeout=timeout,
            lane_key=lane_key,
        )

    def acquire_preparation(
        self,
        *,
        cancel_event: EventState | None = None,
        pause_event: EventState | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        timeout: float | None = None,
        lane_key: Hashable | None = None,
    ) -> AiRequestLease:
        """Acquire independent preparation capacity; release before admission.

        Never hold this lease while waiting for a request lease. Preparation
        callbacks should only prepare data, not issue nested model calls.
        """

        return self._acquire(
            preparation=True,
            cancel_event=cancel_event,
            pause_event=pause_event,
            is_cancelled=is_cancelled,
            timeout=timeout,
            lane_key=lane_key,
        )

    def _acquire(
        self,
        *,
        preparation: bool,
        cancel_event: EventState | None,
        pause_event: EventState | None,
        is_cancelled: Callable[[], bool] | None,
        timeout: float | None,
        lane_key: Hashable | None,
    ) -> AiRequestLease:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        queue = self._preparation if preparation else self._requests
        waiter = _Waiter(self.capture_lane() if lane_key is None else lane_key, pause_event)

        with self._condition:
            queue.enqueue(waiter)
            try:
                while True:
                    if self._cancelled(cancel_event, is_cancelled):
                        raise AiRequestCancelledError("AI request cancelled while waiting for concurrency budget")

                    if queue.active < queue.limit and queue.next_ready() is waiter:
                        # Recheck after observing the free slot so a cancellation
                        # predicate always wins over provider admission.
                        if self._cancelled(cancel_event, is_cancelled):
                            raise AiRequestCancelledError("AI request cancelled while waiting for concurrency budget")
                        queue.admit(waiter)
                        return AiRequestLease(self, preparation=preparation)

                    wait_seconds = self._STATE_POLL_SECONDS
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("timed out waiting for AI request concurrency budget")
                        wait_seconds = min(wait_seconds, remaining)
                    self._condition.wait(wait_seconds)
            finally:
                queue.remove(waiter)
                self._condition.notify_all()

    def notify_state_changed(self) -> None:
        """Wake waiters after an external cancellation or pause state change."""

        with self._condition:
            self._condition.notify_all()

    @staticmethod
    def _cancelled(cancel_event: EventState | None, predicate: Callable[[], bool] | None) -> bool:
        return (cancel_event is not None and cancel_event.is_set()) or (predicate is not None and predicate())

    def _release(self, *, preparation: bool = False) -> None:
        with self._condition:
            queue = self._preparation if preparation else self._requests
            if queue.active <= 0:
                raise RuntimeError("AI request budget released without an active lease")
            queue.active -= 1
            self._condition.notify_all()


__all__ = [
    "AiRequestBudget",
    "AiRequestBudgetSnapshot",
    "AiRequestCancelledError",
    "AiRequestLease",
    "EventState",
]
