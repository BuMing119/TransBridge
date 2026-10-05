from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.terminology_closure import ProofreadTerminologyClosure
from transbridge.infra.limited_llm_client import LimitedLLMClient


def _run(refiner, *, count=6, workers=2, progress=None, stop=None):
    before = tuple(
        PostProcessCandidate(
            run_id="concurrency",
            entry_key=EntryKey(SourceNamespace("plugin"), str(index)),
            before_revision=EntryRevision(),
            original="Dragon",
            before_text="旧译",
            text="旧译",
            stage=1,
        )
        for index in range(count)
    )
    stage = ProofreadTerminologyClosure(refiner, model="unknown", max_tokens_per_batch=10000, max_items=1)
    return stage.apply(
        before,
        tuple(candidate.with_text("龙", "proofread") for candidate in before),
        {candidate.entry_key: {"Dragon": "巨龙"} for candidate in before},
        max_workers=workers,
        progress_callback=progress,
        is_cancelled=(stop or threading.Event()).is_set,
    )


def _results(entries, **kwargs):
    return {entry.id: SimpleNamespace(refined_translation="巨龙", valid=True, **kwargs) for entry in entries}


def _wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        threading.Event().wait(0.005)
    assert predicate()


def test_parallel_batches_complete_out_of_order_but_results_and_progress_remain_correct():
    barrier = threading.Barrier(2)
    first_progress = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    owner = threading.get_ident()
    progress = []

    class Refiner:
        def refine_batch(self, entries, issues_map, *, terms_map):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                key = entries[0].key
                if key in {"0", "1"}:
                    barrier.wait(timeout=3)
                if key == "0":
                    assert first_progress.wait(3)
                if key == "1":
                    raise RuntimeError("one batch failed")
                return _results(entries)
            finally:
                with lock:
                    active -= 1

    def on_progress(current, total, message):
        assert threading.get_ident() == owner
        if message.startswith("术语修复已处理"):
            progress.append(current)
            if current:
                first_progress.set()

    candidates, diagnostics = _run(Refiner(), progress=on_progress)
    assert peak == 2
    assert [candidate.entry_key.local_key for candidate in candidates] == list(map(str, range(6)))
    assert [candidate.accepted for candidate in candidates] == [True, False, True, True, True, True]
    assert candidates[1].text == "旧译"
    assert progress == [0, 1, 2, 3, 4, 5, 6, 6]
    assert dict(diagnostics[0].details)["error_type"] == "RuntimeError"


@pytest.mark.parametrize("cancel", [False, True])
def test_paused_refinement_shares_budget_with_translation_and_cancels_waiters(cancel):
    pause, stop, release, started, two_active = (threading.Event() for _ in range(5))
    budget = AiRequestBudget(2)
    translation_lease = budget.acquire()
    lock = threading.Lock()
    active = calls = 0

    def chat(*_args):
        nonlocal active, calls
        with lock:
            active += 1
            calls += 1
            if active == 2:
                two_active.set()
        started.set()
        try:
            assert release.wait(3)
            return "巨龙"
        finally:
            with lock:
                active -= 1

    client = LimitedLLMClient(SimpleNamespace(chat=chat), budget, cancel_event=stop, pause_event=pause)

    class Refiner:
        def refine_batch(self, entries, issues_map, *, terms_map):
            client.chat([])
            return _results(entries)

    with ThreadPoolExecutor(max_workers=1) as runner:
        future = runner.submit(_run, Refiner(), workers=3, stop=stop)
        try:
            _wait_until(lambda: budget.snapshot().waiting == 3)
            assert calls == 0
            if cancel:
                stop.set()
                budget.notify_state_changed()
            else:
                pause.set()
                budget.notify_state_changed()
                assert started.wait(3)
                assert budget.snapshot().in_flight == 2  # one translation + one refinement
                assert calls == 1
                translation_lease.release()
                assert two_active.wait(3)
        finally:
            release.set()
            translation_lease.release()
        candidates, diagnostics = future.result(timeout=3)
    assert budget.snapshot().in_flight == 0 and budget.snapshot().waiting == 0
    if cancel:
        assert calls == 0
        assert all(not candidate.accepted and candidate.text == "龙" for candidate in candidates)
        assert diagnostics[-1].code == "PROOFREAD_REFINEMENT_CANCELLED"
    else:
        assert all(candidate.accepted for candidate in candidates)
        assert budget.snapshot().peak_in_flight == 2


def test_cancelled_result_stops_submission_and_waits_for_active_batches(monkeypatch):
    from transbridge.application.translation import terminology_refinement as scheduling

    barrier = threading.Barrier(2)
    release = threading.Event()
    cancelling = threading.Event()
    submitted = []

    class Pool(ThreadPoolExecutor):
        def submit(self, fn, batch):
            submitted.append(batch.index)
            return super().submit(fn, batch)

        def shutdown(self, **kwargs):
            cancelling.set()
            return super().shutdown(**kwargs)

    monkeypatch.setattr(scheduling, "ThreadPoolExecutor", Pool)

    class Refiner:
        def refine_batch(self, entries, issues_map, *, terms_map):
            barrier.wait(timeout=3)
            if entries[0].key == "0":
                return _results(entries, failure_code="cancelled")
            assert release.wait(3)
            return _results(entries)

    with ThreadPoolExecutor(max_workers=1) as runner:
        future = runner.submit(_run, Refiner(), count=100)
        try:
            assert cancelling.wait(3)
            assert submitted == [0, 1]
            assert not future.done()
        finally:
            release.set()
        candidates, diagnostics = future.result(timeout=3)
    assert all(not candidate.accepted and candidate.text == "龙" for candidate in candidates)
    assert diagnostics[-1].code == "PROOFREAD_REFINEMENT_CANCELLED"


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_concurrency_is_rejected(workers):
    with pytest.raises(ValueError, match="max_workers"):
        _run(None, workers=workers)


@pytest.mark.parametrize("workers", [1, 2])
def test_cancellation_at_final_progress_retains_candidate_without_approval(workers):
    stop = threading.Event()
    progress = []

    class Refiner:
        def refine_batch(self, entries, issues_map, *, terms_map):
            return _results(entries)

    def on_progress(current, total, message):
        progress.append(message)
        if current == total and message.startswith("术语修复已处理"):
            stop.set()

    candidates, diagnostics = _run(Refiner(), count=1, workers=workers, stop=stop, progress=on_progress)
    assert not candidates[0].accepted and candidates[0].text == "巨龙"
    assert diagnostics[-1].code == "PROOFREAD_REFINEMENT_CANCELLED"
    assert "正在整理结果" not in progress[-1]
