"""Advisory review uses the same bounded scheduling as translation."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.infra.limited_llm_client import LimitedLLMClient


def _candidates(count=6):
    return tuple(
        PostProcessCandidate(
            "concurrency",
            EntryKey(SourceNamespace("plugin"), str(index)),
            EntryRevision(),
            "Guards",
            "卫兵",
            "卫兵",
            1,
        )
        for index in range(count)
    )


def _response(messages):
    entries = json.loads(messages[1]["content"])["entries"]
    return json.dumps({
        "results": [
            {
                "entry_key": entry["entry_key"],
                "final_translation": "卫兵",
                "review_status": "term_suspect",
                "review_reason": "Guards 在此语境指卫兵，参考术语瓜尔不适用",
            }
            for entry in entries
        ]
    })


def _wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        threading.Event().wait(0.005)
    assert predicate()


def test_parallel_reviews_keep_input_order_and_never_start_term_repair():
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = peak = calls = 0

    class Client:
        def chat(self, messages, _max_tokens):
            nonlocal active, peak, calls
            key = json.loads(messages[1]["content"])["entries"][0]["entry_key"]["local_key"]
            with lock:
                active += 1
                calls += 1
                peak = max(peak, active)
            try:
                if key in {"0", "1"}:
                    barrier.wait(timeout=3)
                return _response(messages)
            finally:
                with lock:
                    active -= 1

    progress = []
    stage = ProofreadStage(Client(), term_resolver=lambda _: {"Guards": "瓜尔"}, max_items=1)
    outcome = stage.run(_candidates(), max_workers=2, progress_callback=lambda c, *_: progress.append(c))
    assert peak == 2 and calls == 6
    assert [item.entry_key.local_key for item in outcome.candidates] == list(map(str, range(6)))
    assert all(item.accepted and item.text == "卫兵" for item in outcome.candidates)
    assert progress == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("cancel", [False, True])
def test_paused_review_shares_request_budget_with_translation(cancel):
    pause, stop, release, started, two_active = (threading.Event() for _ in range(5))
    budget = AiRequestBudget(2)
    translation_lease = budget.acquire()
    lock = threading.Lock()
    active = calls = 0

    class Client:
        def chat(self, messages, *_args, **_kwargs):
            nonlocal active, calls
            with lock:
                active += 1
                calls += 1
                if active == 2:
                    two_active.set()
            started.set()
            try:
                assert release.wait(3)
                return _response(messages)
            finally:
                with lock:
                    active -= 1

    client = LimitedLLMClient(Client(), budget, cancel_event=stop, pause_event=pause)
    stage = ProofreadStage(client, term_resolver=lambda _: {"Guards": "瓜尔"}, max_items=1)
    with ThreadPoolExecutor(max_workers=1) as runner:
        future = runner.submit(stage.run, _candidates(), max_workers=3)
        try:
            _wait_until(lambda: budget.snapshot().waiting_preparation == 3)
            assert calls == 0
            if cancel:
                stop.set()
                budget.notify_state_changed()
            else:
                pause.set()
                budget.notify_state_changed()
                assert started.wait(3)
                assert budget.snapshot().in_flight == 2
                translation_lease.release()
                assert two_active.wait(3)
        finally:
            release.set()
            pause.set()
            budget.notify_state_changed()
            translation_lease.release()
        outcome = future.result(timeout=3)
    assert budget.snapshot().in_flight == 0 and budget.snapshot().waiting == 0
    if cancel:
        assert calls == 0
        assert all(not item.accepted and item.text == "卫兵" for item in outcome.candidates)
    else:
        assert all(item.accepted for item in outcome.candidates)
        assert budget.snapshot().peak_in_flight == 2


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_concurrency_is_rejected(workers):
    with pytest.raises(ValueError, match="max_workers"):
        ProofreadStage(object()).run(_candidates(), max_workers=workers)
