from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation import proofread_batch_dispatch
from transbridge.application.translation._open_proofread_stage import ProofreadStage
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
from transbridge.application.translation.postprocess import PostProcessCandidate


def _candidates(count):
    return tuple(
        PostProcessCandidate(
            run_id="run",
            entry_key=EntryKey(SourceNamespace.legacy(), str(index)),
            before_revision=EntryRevision(),
            original="Source",
            before_text="Current",
            text="Current",
            stage=2,
        )
        for index in range(count)
    )


def test_cancel_stops_dispatch_and_keeps_completed_candidates():
    candidates = _candidates(100)
    calls = []

    class Client:
        def chat(self, messages, _tokens):
            entry = json.loads(messages[1]["content"])["entries"][0]
            calls.append(entry["entry_key"])
            if len(calls) == 2:
                raise AiRequestCancelledError("cancelled")
            return json.dumps({"results": [{"entry_key": entry["entry_key"], "final_translation": "Updated"}]})

    outcome = ProofreadStage(Client(), max_items=1)(candidates)

    assert len(calls) == 2
    assert outcome.candidates[0].text == "Updated"
    assert outcome.candidates[0].accepted
    assert all(not item.accepted for item in outcome.candidates[1:])
    diagnostics = {item.code: dict(item.details) for item in outcome.diagnostics}
    assert len(diagnostics["PROOFREAD_LLM_CALL_CANCELLED"]["entry_keys"]) == 1
    assert len(diagnostics["PROOFREAD_BATCH_NOT_STARTED"]["entry_keys"]) == 98


def test_parallel_cancel_has_bounded_submissions_and_one_call_diagnostic(monkeypatch):
    started = threading.Barrier(4)
    cancelled = threading.Event()
    submissions = []

    class Pool(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            submissions.append(args)
            return super().submit(fn, *args, **kwargs)

    class Client:
        def chat(self, _messages, _tokens):
            started.wait(3)
            assert cancelled.wait(3)
            raise AiRequestCancelledError("cancelled")

        def cancel(self):
            cancelled.set()

    monkeypatch.setattr(proofread_batch_dispatch, "ThreadPoolExecutor", Pool)
    stage = ProofreadStage(Client(), max_items=1, max_workers=3)
    outcomes = []
    thread = threading.Thread(target=lambda: outcomes.append(stage(_candidates(100))))
    thread.start()
    try:
        started.wait(3)
        assert len(submissions) == 3
        stage.cancel()
    finally:
        cancelled.set()
        thread.join(3)
    assert not thread.is_alive()
    assert len(submissions) == 3
    assert len(outcomes) == 1
    diagnostics = {item.code: dict(item.details) for item in outcomes[0].diagnostics}
    assert len(outcomes[0].diagnostics) == 2
    assert len(diagnostics["PROOFREAD_LLM_CALL_CANCELLED"]["entry_keys"]) == 3
    assert len(diagnostics["PROOFREAD_BATCH_NOT_STARTED"]["entry_keys"]) == 97
