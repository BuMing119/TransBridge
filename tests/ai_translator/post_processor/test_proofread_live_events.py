from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.application.contracts import Diagnostic
from transbridge.application.translation.postprocess import PostProcessStageOutcome
from transbridge.application.translation.proofread_events import ProofreadEventLog
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.converter.translation_entry import TranslationEntry


def _entries(count=2):
    return [TranslationEntry(str(i), str(i), "Hello", "你好", 1, "") for i in range(count)]


def _pipeline(stage):
    return ProofreadPipeline(None, SimpleNamespace(enable_proofread=True), proofread_stage=stage)


def test_progress_is_visible_before_next_batch_finishes():
    logs = []
    second_request = threading.Event()
    release = threading.Event()

    class Client:
        calls = 0

        def chat_prepared(self, prepare, max_tokens=0):
            self.calls += 1
            if self.calls == 2:
                second_request.set()
                assert release.wait(5)
            payload = json.loads(prepare()[1]["content"])
            return json.dumps({
                "results": [
                    {"entry_key": item["entry_key"], "final_translation": "您好"} for item in payload["entries"]
                ]
            })

    pipeline = _pipeline(ProofreadStage(Client(), max_items=1))
    results = []
    worker = threading.Thread(target=lambda: results.append(pipeline.process(_entries(), log_callback=logs.append)))
    worker.start()
    try:
        assert second_request.wait(5)
        assert logs[0] == "开始校对，共 2 条"
        assert "校对进度 1/2 条" in logs
        assert not results
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert all(item.accepted for item in results[0].values())
    assert "开始检查术语" in logs
    assert logs[-1] == "校对结束：成功 2 条，未完成 0 条"


def test_routine_progress_is_coalesced_and_recovery_events_are_immediate(monkeypatch):
    from transbridge.application.translation import proofread_events

    monkeypatch.setattr(proofread_events.time, "monotonic", lambda: 10)
    logs = []
    events = ProofreadEventLog(logs.append)
    for completed in range(1, 1001):
        events.progress("校对", completed, 1000)
    events.progress("校对", 1000, 1000)
    assert len(logs) == 10
    events.emit("正在重试 1 条未完成条目")
    assert logs[-1] == "正在重试 1 条未完成条目"


def test_slow_progress_is_logged_without_waiting_for_ten_percent(monkeypatch):
    from transbridge.application.translation import proofread_events

    now = [10]
    monkeypatch.setattr(proofread_events.time, "monotonic", lambda: now[0])
    logs = []
    events = ProofreadEventLog(logs.append)
    events.progress("校对", 1, 1000)
    now[0] += 6
    events.progress("校对", 2, 1000)
    assert logs == ["校对进度 2/1000 条"]


def test_legacy_runner_keeps_diagnostics_without_dumping_them_into_user_log():
    diagnostic = Diagnostic("INTERNAL_DETAIL", "technical diagnostic text")

    def run(candidates, *, max_workers, progress_callback):
        return PostProcessStageOutcome(
            "proofread", tuple(item.with_text("您好", "proofread") for item in candidates), (diagnostic,)
        )

    logs = []
    pipeline = _pipeline(SimpleNamespace(run=run))
    results = pipeline.process(_entries(1), log_callback=logs.append)
    assert results["0"].accepted
    assert pipeline.diagnostics == (diagnostic,)
    assert logs == ["开始校对，共 1 条", "校对结束：成功 1 条，未完成 0 条"]


def test_event_callback_failure_does_not_discard_successful_candidates(caplog):
    def run(candidates, *, event_callback, **kwargs):
        event_callback("已恢复 1 条")
        return PostProcessStageOutcome("proofread", tuple(item.with_text("您好", "proofread") for item in candidates))

    def broken_callback(_message):
        raise RuntimeError("widget disconnected")

    results = _pipeline(SimpleNamespace(run=run)).process(_entries(1), log_callback=broken_callback)
    assert results["0"].accepted
    assert "Proofreading event callback failed" in caplog.text
    assert "widget disconnected" in caplog.text


def test_cancelled_before_start_emits_terminal_event_without_request():
    stop = threading.Event()
    stop.set()
    logs = []
    results = _pipeline(object()).process(_entries(1), log_callback=logs.append, stop_event=stop)
    assert logs[-1] == "校对已取消"
    assert results["0"].processing_status == "not_started"
