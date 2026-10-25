from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", True])
def test_budget_rejects_invalid_max_preparing(value) -> None:
    with pytest.raises(ValueError, match="max_preparing must be a positive integer"):
        AiRequestBudget(3, max_preparing=value)


def test_preparation_capacity_is_separate_and_bounded() -> None:
    budget = AiRequestBudget(1)
    with budget.acquire_preparation():
        assert budget.snapshot().preparing == 1
        assert budget.snapshot().in_flight == 0
        with pytest.raises(TimeoutError):
            budget.acquire_preparation(timeout=0)
        with budget.acquire(timeout=0):
            assert budget.snapshot().in_flight == 1
        assert budget.snapshot().waiting_preparation == 0
    assert budget.snapshot().preparing == 0
    assert budget.snapshot().peak_preparing == 1
    assert AiRequestBudget(30).max_preparing == 4


@pytest.mark.parametrize("preparation", [False, True])
def test_busy_lane_cannot_starve_other_source_or_stage(preparation: bool) -> None:
    budget = AiRequestBudget(1)
    acquire = budget.acquire_preparation if preparation else budget.acquire
    order: list[str] = []
    occupied = acquire()

    def run(lane: str, name: str) -> None:
        with acquire(lane_key=lane):
            order.append(name)

    def waiting() -> int:
        snapshot = budget.snapshot()
        return snapshot.waiting_preparation if preparation else snapshot.waiting

    # Enqueue deterministically, with all busy-source work ahead of the other
    # source's translation and proofread. Plain FIFO would starve both stages.
    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = []
        requests = [
            ("A:translate", "A1"),
            ("A:translate", "A2"),
            ("A:translate", "A3"),
            ("A:translate", "A4"),
            ("B:translate", "B1"),
            ("B:proofread", "P1"),
            ("B:proofread", "P2"),
        ]
        try:
            for index, (lane, name) in enumerate(requests, start=1):
                futures.append(executor.submit(run, lane, name))
                _wait_until(lambda: waiting() == index)
        finally:
            occupied.release()
        for future in futures:
            future.result(timeout=2)

    assert order == ["A1", "B1", "P1", "A2", "P2", "A3", "A4"]
    assert waiting() == 0


def test_paused_lane_does_not_block_ready_lane_or_reserve_capacity() -> None:
    budget = AiRequestBudget(2)
    paused = threading.Event()
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(budget.acquire, lane_key="paused", pause_event=paused, cancel_event=cancel)
        _wait_until(lambda: budget.snapshot().waiting == 1)
        try:
            with budget.acquire(lane_key="ready", timeout=0), budget.acquire(lane_key="ready", timeout=0):
                assert budget.snapshot().in_flight == 2
        finally:
            cancel.set()
            budget.notify_state_changed()
        with pytest.raises(AiRequestCancelledError):
            future.result(timeout=1)
    assert budget.snapshot().waiting == 0


def test_cancelled_head_releases_next_ready_lane() -> None:
    budget = AiRequestBudget(1)
    cancel = threading.Event()
    occupied = budget.acquire()
    with ThreadPoolExecutor(max_workers=2) as executor:
        cancelled = executor.submit(budget.acquire, lane_key="cancelled", cancel_event=cancel)
        _wait_until(lambda: budget.snapshot().waiting == 1)
        following = executor.submit(budget.acquire, lane_key="following")
        _wait_until(lambda: budget.snapshot().waiting == 2)
        cancel.set()
        budget.notify_state_changed()
        with pytest.raises(AiRequestCancelledError):
            cancelled.result(timeout=1)
        occupied.release()
        following.result(timeout=1).release()
    assert budget.snapshot().in_flight == 0
    assert budget.snapshot().waiting == 0


def test_lane_scope_restores_and_can_be_propagated_into_thread_pool() -> None:
    budget = AiRequestBudget(1)

    def observe(key):
        assert budget.capture_lane() is None
        with budget.lane(key):
            return budget.capture_lane()

    assert budget.capture_lane() is None
    with budget.lane(("plugin-a", "translate")):
        with budget.lane(("plugin-a", "proofread")):
            assert budget.capture_lane() == ("plugin-a", "proofread")
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(observe, budget.capture_lane()).result(timeout=1) == ("plugin-a", "translate")
    assert budget.capture_lane() is None
