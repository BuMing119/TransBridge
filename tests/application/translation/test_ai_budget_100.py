from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from transbridge.application.translation.ai_request_budget import AiRequestBudget, AiRequestCancelledError


def test_100_shared_slots_across_sources_and_stages_release_after_errors_and_cancel():
    budget = AiRequestBudget(100)
    ready = threading.Barrier(101, timeout=10)
    release = threading.Event()
    pause = threading.Event()
    cancel = threading.Event()

    def call(index):
        try:
            with budget.acquire(lane_key=(index % 3, ("translate", "terms", "polish")[index % 3])):
                ready.wait()
                assert release.wait(10)
                if index % 13 == 0:
                    raise ValueError("simulated model failure")
                return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=102) as pool:
        active = [pool.submit(call, i) for i in range(100)]
        try:
            ready.wait()
            assert budget.snapshot().in_flight == 100
            paused = pool.submit(budget.acquire, lane_key="paused", pause_event=pause)
            cancelled = pool.submit(budget.acquire, lane_key="cancelled", cancel_event=cancel)
            deadline = time.monotonic() + 3
            while budget.snapshot().waiting < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert budget.snapshot().waiting == 2
            assert budget.snapshot().in_flight == 100
            cancel.set()
            budget.notify_state_changed()
            with pytest.raises(AiRequestCancelledError):
                cancelled.result(timeout=3)
            assert not paused.done()
        finally:
            release.set()
            pause.set()
            budget.notify_state_changed()
        assert sum(f.result(timeout=5) for f in active) == 92
        paused.result(timeout=3).release()
    assert budget.snapshot().in_flight == budget.snapshot().waiting == 0
