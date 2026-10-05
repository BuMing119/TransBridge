from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome, build_task_record
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


@pytest.mark.parametrize(
    ("responses", "failure_code"),
    [
        ((RuntimeError("unavailable"), RuntimeError("unavailable")), "PROOFREAD_LLM_CALL_FAILED"),
        (("{}", RuntimeError("unavailable")), "PROOFREAD_RECOVERY_EXHAUSTED"),
        (("{}", "{}"), "PROOFREAD_RESPONSE_MALFORMED"),
    ],
    ids=["call-failure", "recovery-exhausted", "malformed-response"],
)
def test_failed_proofread_batch_stays_failed_in_history_after_cancellation(tmp_path, responses, failure_code):
    entries = tuple(TranslationEntry(str(i), str(i), "Hello", "你好", 1, "") for i in range(2))
    called_keys = []
    pending_responses = iter(responses)

    class Client:
        def chat(self, messages, _tokens):
            called_keys.extend(item["entry_key"] for item in json.loads(messages[1]["content"])["entries"])
            response = next(pending_responses)
            if isinstance(response, Exception):
                raise response
            return response

    stage = ProofreadStage(Client(), max_items=1)
    pipeline = ProofreadPipeline(None, SimpleNamespace(enable_proofread=True), proofread_stage=stage)
    stop = threading.Event()

    def progress(_phase, completed, _total, _message):
        if completed == 1:
            stop.set()
            stage.cancel()

    results = pipeline.process(entries, stop_event=stop, progress_callback=progress, max_workers=1)

    assert called_keys == [entries[0].identity.to_dict()] * 2
    assert results["0"].processing_status == "failed"
    assert failure_code in results["0"].note
    assert results["1"].processing_status == "not_started"
    assert failure_code not in results["1"].note
    assert all(not result.accepted for result in results.values())
    assert all(entry.translation == "你好" for entry in entries)

    task = SourceTask("source", "Source", None, None, (), entries)
    outcome = SourceOutcome(
        task,
        polish=results,
        failed_keys=tuple(entry.key for entry in entries),
        diagnostics=pipeline.diagnostics,
        cancelled=True,
    )
    record = build_task_record([outcome], SimpleNamespace(run_id="failed-then-cancelled"), state="cancelled")
    store = TaskHistoryStore(tmp_path)
    store.save(record)
    restored = store.load(record["run_id"])

    assert restored["counts"] == {"successful": 0, "failed": 1, "unprocessed": 1, "cancelled": 0}
    snapshot = restored["sources"][0]["snapshot"]
    assert failure_code in snapshot["entries"][0]["report_details"]["note"]
    assert failure_code not in snapshot["entries"][1]["report_details"]["note"]
    failure = next(item for item in snapshot["diagnostics"] if item["code"] == failure_code)
    assert failure["details"]["entry_keys"] == [entries[0].identity.to_dict()]
