from __future__ import annotations

import threading
import time

import pytest

from transbridge.application.translation.ai_request_budget import (
    AiRequestBudget,
    AiRequestCancelledError,
)


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", None, True])
def test_budget_rejects_invalid_max_in_flight(value) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        AiRequestBudget(value)


def test_snapshot_tracks_waiting_in_flight_peak_and_idempotent_release() -> None:
    budget = AiRequestBudget(1)
    first = budget.acquire()
    acquired = threading.Event()

    def wait_for_slot() -> None:
        with budget.acquire():
            acquired.set()

    thread = threading.Thread(target=wait_for_slot)
    thread.start()
    _wait_until(lambda: budget.snapshot().waiting == 1)

    assert budget.snapshot().in_flight == 1
    assert budget.snapshot().peak_in_flight == 1

    first.release()
    first.release()
    assert acquired.wait(1)
    thread.join(1)

    assert budget.snapshot().in_flight == 0
    assert budget.snapshot().waiting == 0
    assert budget.snapshot().peak_in_flight == 1


def test_paused_waiter_does_not_acquire_until_resumed() -> None:
    budget = AiRequestBudget(1)
    pause_event = threading.Event()
    acquired = threading.Event()

    def wait_for_resume() -> None:
        with budget.acquire(pause_event=pause_event):
            acquired.set()

    thread = threading.Thread(target=wait_for_resume)
    thread.start()
    _wait_until(lambda: budget.snapshot().waiting == 1)
    assert not acquired.is_set()
    assert budget.snapshot().in_flight == 0

    pause_event.set()
    budget.notify_state_changed()
    assert acquired.wait(1)
    thread.join(1)


def test_cancelled_waiter_exits_without_acquiring() -> None:
    budget = AiRequestBudget(1)
    cancel_event = threading.Event()
    first = budget.acquire()
    errors: list[Exception] = []

    def wait_for_slot() -> None:
        try:
            budget.acquire(cancel_event=cancel_event)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=wait_for_slot)
    thread.start()
    _wait_until(lambda: budget.snapshot().waiting == 1)
    cancel_event.set()
    budget.notify_state_changed()
    thread.join(1)

    assert len(errors) == 1
    assert isinstance(errors[0], AiRequestCancelledError)
    assert budget.snapshot().in_flight == 1
    assert budget.snapshot().waiting == 0
    first.release()


def test_acquire_timeout_leaves_counters_consistent() -> None:
    budget = AiRequestBudget(1)
    with budget.acquire():
        with pytest.raises(TimeoutError):
            budget.acquire(timeout=0.01)
        assert budget.snapshot().in_flight == 1
        assert budget.snapshot().waiting == 0
