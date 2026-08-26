"""Run-scoped admission control for outbound AI requests.

The budget is deliberately independent from Qt and provider SDKs.  A lease
covers one logical ``chat``/``chat_stream`` invocation, including any retries
performed internally by the provider client.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    """Thread-safe point-in-time budget diagnostics."""

    in_flight: int
    waiting: int
    peak_in_flight: int


class AiRequestLease:
    """A single, idempotently releasable request-budget lease."""

    def __init__(self, budget: AiRequestBudget) -> None:
        self._budget = budget
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
        self._budget._release()


class AiRequestBudget:
    """Limit the number of provider requests in flight for one AI run.

    ``pause_event`` follows the worker convention used by TransBridge:
    ``set`` means running and ``clear`` means paused.  Cancellation is checked
    while waiting and immediately before a lease is granted.
    """

    _STATE_POLL_SECONDS = 0.05

    def __init__(self, max_in_flight: int) -> None:
        if isinstance(max_in_flight, bool) or not isinstance(max_in_flight, int) or max_in_flight <= 0:
            raise ValueError("max_in_flight must be a positive integer")
        self._max_in_flight = max_in_flight
        self._condition = threading.Condition()
        self._in_flight = 0
        self._waiting = 0
        self._peak_in_flight = 0

    @property
    def max_in_flight(self) -> int:
        return self._max_in_flight

    def snapshot(self) -> AiRequestBudgetSnapshot:
        with self._condition:
            return AiRequestBudgetSnapshot(
                in_flight=self._in_flight,
                waiting=self._waiting,
                peak_in_flight=self._peak_in_flight,
            )

    def acquire(
        self,
        *,
        cancel_event: EventState | None = None,
        pause_event: EventState | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> AiRequestLease:
        """Wait for admission and return a lease.

        Args:
            cancel_event: An event whose set state cancels this admission.
            pause_event: An event whose clear state prevents new admission.
            is_cancelled: Optional additional cancellation predicate.
            timeout: Maximum wait in seconds. ``None`` waits indefinitely.

        Raises:
            AiRequestCancelledError: Cancellation wins before admission.
            TimeoutError: The optional timeout expires before admission.
        """

        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._condition:
            self._waiting += 1
            try:
                while True:
                    if self._cancelled(cancel_event, is_cancelled):
                        raise AiRequestCancelledError("AI request cancelled while waiting for concurrency budget")

                    running = pause_event is None or pause_event.is_set()
                    if running and self._in_flight < self._max_in_flight:
                        # Recheck after observing the free slot so a cancellation
                        # predicate always wins over provider admission.
                        if self._cancelled(cancel_event, is_cancelled):
                            raise AiRequestCancelledError("AI request cancelled while waiting for concurrency budget")
                        self._in_flight += 1
                        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
                        return AiRequestLease(self)

                    wait_seconds = self._STATE_POLL_SECONDS
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("timed out waiting for AI request concurrency budget")
                        wait_seconds = min(wait_seconds, remaining)
                    self._condition.wait(wait_seconds)
            finally:
                self._waiting -= 1

    def notify_state_changed(self) -> None:
        """Wake waiters after an external cancellation or pause state change."""

        with self._condition:
            self._condition.notify_all()

    @staticmethod
    def _cancelled(cancel_event: EventState | None, predicate: Callable[[], bool] | None) -> bool:
        return (cancel_event is not None and cancel_event.is_set()) or (predicate is not None and predicate())

    def _release(self) -> None:
        with self._condition:
            if self._in_flight <= 0:
                raise RuntimeError("AI request budget released without an active lease")
            self._in_flight -= 1
            self._condition.notify_all()


__all__ = [
    "AiRequestBudget",
    "AiRequestBudgetSnapshot",
    "AiRequestCancelledError",
    "AiRequestLease",
    "EventState",
]
