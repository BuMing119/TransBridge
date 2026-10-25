from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from transbridge.ai_translator.batch_execution import BatchExecutor, quest_batch_chains
from transbridge.ai_translator.batch_planner import Batch


def _wait_until(predicate, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", None, True])
def test_executor_requires_positive_worker_count(value) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        BatchExecutor(value)


def test_quest_chains_preserve_known_order_and_keep_unknown_batches_independent() -> None:
    a1 = Batch([], "对话", "quest-a")
    a2 = Batch([], "对话", "quest-a")
    b1 = Batch([], "对话", "quest-b")
    unknown1 = Batch([], "对话")
    unknown2 = Batch([], "对话")
    assert quest_batch_chains([a1, unknown1, b1, a2, unknown2]) == [[a1, a2], [unknown1], [b1], [unknown2]]


def test_shared_worker_limit_and_explicit_chain_dependencies() -> None:
    release = threading.Event()
    barrier = threading.Barrier(3)
    started: list[str] = []
    finished: set[str] = set()
    lock = threading.Lock()

    def operation(item: str) -> None:
        with lock:
            started.append(item)
            if item == "A2":
                assert "A1" in finished
        if item in {"A1", "B1"}:
            barrier.wait(timeout=2)
            assert release.wait(2)
        with lock:
            finished.add(item)

    with BatchExecutor(2) as batches, ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(batches.run, [["A1", "A2"], ["B1"], ["C1"]], operation, lane_key="plugin")
        try:
            barrier.wait(timeout=2)
            assert set(started) == {"A1", "B1"}
            assert batches.snapshot().active_batches == 2
        finally:
            release.set()
        future.result(timeout=2)
        assert batches.snapshot().active_batches == 0
        assert batches.snapshot().peak_active_batches == 2
    assert finished == {"A1", "A2", "B1", "C1"}


def test_source_lanes_rotate_after_each_batch_instead_of_whole_quest() -> None:
    pause = threading.Event()
    order: list[str] = []
    with BatchExecutor(1) as batches, ThreadPoolExecutor(max_workers=2) as callers:
        first = callers.submit(batches.run, [["A1", "A2", "A3"]], order.append, lane_key="A", pause_event=pause)
        _wait_until(lambda: batches.snapshot().pending_runs == 1)
        second = callers.submit(batches.run, [["B1", "B2"]], order.append, lane_key="B", pause_event=pause)
        _wait_until(lambda: batches.snapshot().pending_runs == 2)
        pause.set()
        batches.notify_state_changed()
        first.result(timeout=2)
        second.result(timeout=2)
    assert order == ["A1", "B1", "A2", "B2", "A3"]


def test_finished_source_can_start_next_round_while_other_source_still_runs() -> None:
    slow_started = threading.Event()
    release_slow = threading.Event()
    next_round_started = threading.Event()

    def slow(_item) -> None:
        slow_started.set()
        assert release_slow.wait(2)

    def fast_source(batches: BatchExecutor) -> None:
        batches.run([["round1"]], lambda _: None, lane_key="fast")
        batches.run([["round2"]], lambda _: next_round_started.set(), lane_key="fast")

    with BatchExecutor(2) as batches, ThreadPoolExecutor(max_workers=2) as callers:
        slow_future = callers.submit(batches.run, [["slow"]], slow, lane_key="slow")
        try:
            assert slow_started.wait(1)
            fast_future = callers.submit(fast_source, batches)
            assert next_round_started.wait(1)
            assert not slow_future.done()
        finally:
            release_slow.set()
        fast_future.result(timeout=2)
        slow_future.result(timeout=2)


def test_paused_source_does_not_reserve_workers_and_stop_clears_pending() -> None:
    pause = threading.Event()
    stop = threading.Event()
    paused_calls: list[str] = []
    with BatchExecutor(1) as batches, ThreadPoolExecutor(max_workers=2) as callers:
        paused = callers.submit(
            batches.run,
            [["paused"]],
            paused_calls.append,
            pause_event=pause,
            stop_event=stop,
            lane_key="paused",
        )
        _wait_until(lambda: batches.snapshot().pending_runs == 1)
        ready_calls: list[str] = []
        ready = callers.submit(batches.run, [["ready1"], ["ready2"]], ready_calls.append, lane_key="ready")
        ready.result(timeout=1)
        assert ready_calls == ["ready1", "ready2"]
        assert paused_calls == []
        stop.set()
        batches.notify_state_changed()
        paused.result(timeout=1)
    assert paused_calls == []


def test_stop_waits_for_active_batch_and_never_starts_its_successor() -> None:
    stop = threading.Event()
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def operation(item: str) -> None:
        calls.append(item)
        started.set()
        assert release.wait(2)

    with BatchExecutor(1) as batches, ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(batches.run, [["first", "successor"], ["other"]], operation, stop_event=stop)
        try:
            assert started.wait(1)
            stop.set()
            batches.notify_state_changed()
            _wait_until(lambda: batches.snapshot().ready_chains == 0)
            assert not future.done()
        finally:
            release.set()
        future.result(timeout=2)
    assert calls == ["first"]


def test_failure_stops_only_its_source_and_drains_already_active_batches() -> None:
    failing_started = threading.Event()
    fail = threading.Event()
    sibling_started = threading.Event()
    release_sibling = threading.Event()
    expected = ValueError("batch failed")
    calls: list[str] = []

    def failing_source(item: str) -> None:
        calls.append(item)
        if item == "fails":
            failing_started.set()
            assert fail.wait(2)
            raise expected
        if item == "running":
            sibling_started.set()
            assert release_sibling.wait(2)

    with BatchExecutor(2) as batches, ThreadPoolExecutor(max_workers=2) as callers:
        failing = callers.submit(
            batches.run, [["fails", "no-successor"], ["running"], ["unstarted"]], failing_source, lane_key="A"
        )
        try:
            assert failing_started.wait(1)
            assert sibling_started.wait(1)
            other_calls: list[str] = []
            healthy = callers.submit(batches.run, [["healthy1", "healthy2"]], other_calls.append, lane_key="B")
            _wait_until(lambda: batches.snapshot().pending_runs == 2)
            fail.set()
            healthy.result(timeout=1)
            assert not failing.done()
            assert other_calls == ["healthy1", "healthy2"]
            assert set(calls) == {"fails", "running"}
        finally:
            fail.set()
            release_sibling.set()
        with pytest.raises(ValueError, match="batch failed") as caught:
            failing.result(timeout=2)
        assert caught.value is expected
    assert set(calls) == {"fails", "running"}


def test_dispatch_window_does_not_materialize_all_batches_or_chains() -> None:
    yielded = []
    started = threading.Event()
    release = threading.Event()
    stop = threading.Event()

    def chains():
        for index in range(1000):
            yielded.append(index)
            yield [index]

    def operation(_item) -> None:
        started.set()
        assert release.wait(2)

    with BatchExecutor(1) as batches, ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(batches.run, chains(), operation, stop_event=stop)
        try:
            assert started.wait(1)
            assert yielded == [0, 1]
            assert batches.snapshot().ready_chains == 1
            stop.set()
        finally:
            release.set()
        future.result(timeout=2)
    assert yielded == [0, 1]


def test_nested_run_fails_promptly_without_leaking_worker() -> None:
    with BatchExecutor(1) as batches:
        with pytest.raises(RuntimeError, match="cannot nest"):
            batches.run([["outer"]], lambda _: batches.run([["inner"]], lambda _: None))
        calls = []
        batches.run([["after"]], calls.append)
        assert calls == ["after"]


def test_generator_failure_is_propagated_and_executor_remains_usable() -> None:
    def broken_chain():
        yield "first"
        raise LookupError("broken chain")

    calls = []
    with BatchExecutor(1) as batches:
        with pytest.raises(LookupError, match="broken chain"):
            batches.run([broken_chain()], calls.append)
        batches.run([[], ["after"], []], calls.append)
    assert calls == ["first", "after"]


def test_shutdown_releases_paused_run_and_joins_its_threads() -> None:
    pause = threading.Event()
    batches = BatchExecutor(1)
    calls = []
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(batches.run, [["never"]], calls.append, pause_event=pause)
        _wait_until(lambda: batches.snapshot().pending_runs == 1)
        batches.shutdown()
        future.result(timeout=1)
    assert calls == []
    assert batches.snapshot().active_batches == 0
    with pytest.raises(RuntimeError, match="shut down"):
        batches.run([["late"]], calls.append)
    batches.shutdown()
