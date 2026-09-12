from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

from transbridge.infra.llm_tool_calling import LlmTurn
from transbridge.infra.llm_usage import LlmUsage
from transbridge.smart_assistant.chat_worker import ChatWorker
from transbridge.smart_assistant.observability.collector import ObservabilityCollector
from transbridge.smart_assistant.observability.models import ReActRound


def test_worker_records_usage_once_and_does_not_emit_duplicate_estimate():
    usage = LlmUsage(source="reported", completeness="complete", input_tokens=8, output_tokens=2)

    class Client:
        def chat_stream_with_tools(self, messages, max_tokens, tools, callback, *, usage_callback, purpose):
            usage_callback(usage)
            usage_callback(usage)
            callback("ok")
            return LlmTurn(text="ok", usage=usage)

    worker = ChatWorker(Client(), [], tools=[], purpose="routing")
    records, estimates, results = [], [], []
    worker.on_usage = records.append
    worker.on_token_usage = lambda *args: estimates.append(args)
    worker.on_finished = results.append
    worker.run()
    assert records == [usage]
    assert estimates == []
    assert results[0].text == "ok"


def test_cancelled_worker_accepts_late_usage_but_not_business_result():
    usage = LlmUsage(source="reported", completeness="complete", input_tokens=50, output_tokens=4)

    class Client:
        def chat_stream_with_tools(self, messages, max_tokens, tools, callback, *, usage_callback, purpose):
            worker.cancel()
            usage_callback(usage)
            return LlmTurn(text="stale", usage=usage)

        def cancel(self):
            pass

    worker = ChatWorker(Client(), [], tools=[])
    records, results = [], []
    worker.on_usage, worker.on_finished = records.append, results.append
    worker.run()
    assert results == []
    assert len(records) == 1
    assert records[0].input_tokens == 50
    assert records[0].outcome == "cancelled"


def test_old_client_signature_gets_unknown_usage_without_replay():
    calls = []

    class Client:
        def chat_stream_with_tools(self, messages, max_tokens, tools, callback):
            calls.append(1)
            raise TypeError("provider rejected request")

    worker = ChatWorker(Client(), [], tools=[])
    records = []
    worker.on_usage = records.append
    worker.run()
    assert calls == [1]
    assert records[0].source == "unknown"
    assert records[0].input_tokens is None
    assert records[0].outcome == "error"


def test_cancel_before_start_does_not_manufacture_attempt():
    worker = ChatWorker(None, [])
    records = []
    worker.on_usage = records.append
    worker.cancel()
    worker.run()
    assert records == []


def test_collector_deduplicates_attempts_and_separates_legacy_estimates():
    collector = ObservabilityCollector()
    collector.start_conversation("synthetic")
    usage = LlmUsage(source="reported", completeness="complete", input_tokens=10, output_tokens=3, purpose="summary")
    collector.on_llm_usage(usage)
    collector.on_llm_usage(usage)
    collector.on_llm_usage(replace(usage, attempt_id="retry", retry_of=usage.attempt_id))
    collector.on_llm_tokens("old-client", 100, 20)
    trace = collector.end_conversation()
    assert trace.token_stats.input_tokens == 100
    assert trace.token_stats.usage_totals()["known_input_tokens"] == 20
    assert trace.token_stats.usage_totals()["attempts"] == 2
    assert trace.to_dict()["token_stats"]["legacy_source"] == "estimated"


def test_parallel_usage_delivery_and_serialization_share_a_consistent_snapshot():
    collector = ObservabilityCollector()
    collector.start_conversation("synthetic-concurrent")

    def deliver(index):
        usage = LlmUsage(
            attempt_id=str(index % 20), source="reported", completeness="complete", input_tokens=5, output_tokens=1
        )
        collector.on_llm_usage(usage)
        snapshot = collector._session_tokens.to_dict()
        assert len(snapshot["usage_attempts"]) == snapshot["usage_totals"]["attempts"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(deliver, range(100)))
    trace = collector.end_conversation()
    assert trace.token_stats.usage_totals()["known_input_tokens"] == 100


def test_captured_late_usage_belongs_to_original_trace_stats_and_round(tmp_path):
    notifications = []
    collector = ObservabilityCollector(on_token_stats_updated=notifications.append)
    collector.start_conversation("old")
    old_trace, old_stats = collector._active, collector._session_tokens
    old_round = collector._current_round = ReActRound()
    captured = collector.capture_usage_callback(notify=lambda callback: callback())
    collector.start_conversation("new")
    new_round = collector._current_round = ReActRound()
    collector._storage_dir = tmp_path
    usage = LlmUsage(attempt_id="late", source="reported", completeness="complete", input_tokens=7, output_tokens=2)
    captured(usage)
    captured(usage)

    assert old_trace.token_stats.usage_totals()["known_input_tokens"] == 7
    assert old_stats.usage_totals()["attempts"] == 1
    assert old_round.llm_input_tokens == 7
    assert new_round.llm_input_tokens == 0
    assert collector._session_tokens.usage_totals()["attempts"] == 0
    assert notifications == []
    files = list((tmp_path / "usage-attempts").glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["conv_id"] == "old"
    assert stored["trace_started_at"] == old_trace.started_at
    assert stored["usage"]["input_tokens"] == 7


def test_notification_dispatch_rechecks_attribution_after_switch():
    notifications, queued = [], []
    collector = ObservabilityCollector(on_token_stats_updated=notifications.append)
    collector.start_conversation("old")
    captured = collector.capture_usage_callback(notify=queued.append)
    captured(LlmUsage())
    assert len(queued) == 1
    assert notifications == []
    collector.start_conversation("new")
    queued[0]()
    assert notifications == []


def test_current_usage_notification_runs_only_through_explicit_dispatch():
    notifications, queued = [], []
    collector = ObservabilityCollector(on_token_stats_updated=notifications.append)
    collector.start_conversation("current")
    collector.capture_usage_callback()(LlmUsage())
    assert notifications == []
    collector.capture_usage_callback(notify=queued.append)(LlmUsage())
    assert notifications == []
    queued.pop()()
    assert notifications == [collector._session_tokens]


def test_destroyed_notification_bridge_does_not_drop_persisted_usage(tmp_path, caplog):
    collector = ObservabilityCollector(storage_dir=tmp_path)
    collector.start_conversation("closed-view")

    def destroyed_bridge(_callback):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    captured = collector.capture_usage_callback(notify=destroyed_bridge)
    captured(LlmUsage(attempt_id="closed-bridge"))
    assert collector._session_tokens.usage_totals()["attempts"] == 1
    assert len(list((tmp_path / "usage-attempts").glob("*.json"))) == 1
    assert "accounting was retained" in caplog.text
    assert list((tmp_path / "usage-attempts").glob(".usage-*")) == []


def test_late_usage_does_not_recreate_deleted_trace_or_overwrite_same_id_reopening(tmp_path):
    collector = ObservabilityCollector()
    collector.start_conversation("reused")
    old_trace = collector._active
    captured = collector.capture_usage_callback()
    collector.start_conversation("reused")
    collector._storage_dir = tmp_path
    path = tmp_path / "reused.json"
    path.write_text("new diagnostic trace", encoding="utf-8")
    captured(LlmUsage(attempt_id="after-reopen"))
    assert path.read_text(encoding="utf-8") == "new diagnostic trace"
    path.unlink()
    captured(LlmUsage(attempt_id="after-delete"))
    assert not path.exists()
    assert old_trace.token_stats.usage_totals()["attempts"] == 2
    assert collector._session_tokens.usage_totals()["attempts"] == 0
    assert len(list((tmp_path / "usage-attempts").glob("*.json"))) == 2


def test_usage_storage_failure_does_not_fail_delivery_or_notification(tmp_path, caplog):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("synthetic blocker", encoding="utf-8")
    notifications = []
    collector = ObservabilityCollector(storage_dir=blocked, on_token_stats_updated=notifications.append)
    collector.start_conversation("storage-failed")
    collector.capture_usage_callback(notify=lambda callback: callback())(LlmUsage())
    assert collector._session_tokens.usage_totals()["attempts"] == 1
    assert notifications == [collector._session_tokens]
    assert "in-memory accounting retained" in caplog.text
