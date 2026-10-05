from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import Diagnostic, ErrorCategory, OperationOutcome
from transbridge.application.translation.task_history import TaskHistoryStore, restore_report_snapshot
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator.source_execution import (
    SourceOutcome,
    _PolishResults,
    build_source_snapshot,
    build_task_record,
)
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


def _outcome():
    entry = TranslationEntry("id", "key", "Hello", "你好", 1, "")
    task = SourceTask("source", "Source", None, None, (), (entry,))
    return SourceOutcome(task)


def _request():
    return SimpleNamespace(run_id="../untrusted/run", config=SimpleNamespace(mixed_execution_order="serial"))


def test_unstarted_cancelled_record_keeps_inputs_and_correct_counts():
    record = build_task_record([_outcome()], _request(), state="cancelled")
    snapshot = restore_report_snapshot(record["sources"][0]["snapshot"])
    assert snapshot.outcome is OperationOutcome.CANCELLED
    assert snapshot.failure_count == 0
    assert snapshot.candidates[0].before_text == "你好"
    assert dict(snapshot.candidates[0].report_details)["result_status"] == "not_started"
    assert not any(item.code == "POLISH_ENTRY_FAILED" for item in snapshot.diagnostics)
    assert restore_report_snapshot(snapshot.to_dict()).to_dict() == snapshot.to_dict()
    assert record["counts"] == {"successful": 0, "failed": 0, "unprocessed": 1, "cancelled": 0}
    assert record["ended_at"] is not None


def test_record_counts_validated_candidate_independently_of_application():
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult

    outcome = _outcome()
    outcome.polish = {
        "id": ProofreadResult(
            "id",
            "key",
            "你好",
            "你好",
            0,
            False,
            "Run cancelled",
            "failed",
            processing_status="completed",
            candidate_translation="您好",
        )
    }
    request = _request()
    request.spec = SimpleNamespace(owner=SimpleNamespace(project_id="project", variant_id="version"))
    record = build_task_record([outcome], request, state="cancelled")
    assert record["counts"] == {"successful": 1, "failed": 0, "unprocessed": 0, "cancelled": 0}
    assert not record["applied"] and not record["saved"]
    assert record["variant_id"] == "version"
    candidate = record["sources"][0]["snapshot"]["entries"][0]
    assert candidate["candidate"] == "您好"
    assert candidate["report_details"]["result_status"] == "not_applied"


@pytest.mark.parametrize("before", ["", "原译文"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_translation_failure_survives_empty_or_retained_translation_and_cancellation(before, cancelled, tmp_path):
    from transbridge.ai_translator.translator import TranslationResult
    from transbridge.application.translation.completion_report import build_translation_report_snapshot

    entry = TranslationEntry("entry:1", "key", "Hello", before, 0, "")
    task = SourceTask("source", "Source", None, None, (entry,), ())
    result = TranslationResult(failed_count=1, failed_entries=[f"{entry.id}: provider failed"])
    result.post_process_result = build_translation_report_snapshot(
        result, [entry] if before else [], run_id=_request().run_id, cancelled=cancelled
    )
    outcome = SourceOutcome(task, translation=result, failed_keys=(entry.key,), cancelled=cancelled)
    record = build_task_record([outcome], _request(), state="cancelled" if cancelled else "failed")
    store = TaskHistoryStore(tmp_path)
    store.save(record)
    restored = store.load(record["run_id"])
    assert restored["counts"] == {"successful": 0, "failed": 1, "unprocessed": 0, "cancelled": 0}
    snapshot = restore_report_snapshot(restored["sources"][0]["snapshot"])
    candidate = snapshot.candidates[0]
    assert snapshot.accepted_count == 0 and snapshot.failure_count == 1
    assert candidate.before_text == before and candidate.text == before
    assert not candidate.accepted
    assert dict(candidate.report_details) == {
        "result_status": "failed",
        "processing_status": "failed",
        "note": "entry:1: provider failed",
    }


def test_cancelled_atomic_source_preserves_completed_and_unstarted_translation_counts():
    from transbridge.ai_translator.translator import TranslationResult
    from transbridge.application.translation.completion_report import build_translation_report_snapshot

    completed = TranslationEntry("done", "done", "Hello", "你好", 1, "")
    unstarted = TranslationEntry("unstarted", "unstarted", "World", "", 0, "")
    task = SourceTask("source", "Source", None, None, (completed, unstarted), ())
    result = TranslationResult(success_count=1)
    result.post_process_result = build_translation_report_snapshot(
        result, [completed], run_id=_request().run_id, cancelled=True
    )
    outcome = SourceOutcome(task, translation=result, failed_keys=("done", "unstarted"), cancelled=True)
    record = build_task_record([outcome], _request(), state="cancelled")
    assert record["counts"] == {"successful": 1, "failed": 0, "unprocessed": 1, "cancelled": 0}


def test_source_failure_remains_failed_when_later_source_cancels():
    entry = TranslationEntry("entry", "key", "Hello", "", 0, "")
    task = SourceTask("source", "Source", None, None, (entry,), ())
    outcome = SourceOutcome(task, failed_keys=(entry.key,), error="无法启动翻译")
    record = build_task_record([outcome], _request(), state="cancelled")
    assert record["counts"]["failed"] == 1
    assert record["sources"][0]["snapshot"]["entries"][0]["report_details"]["note"] == "无法启动翻译"


@pytest.mark.parametrize("has_failed_translation", [False, True])
def test_later_polish_exception_does_not_reclassify_completed_translation(has_failed_translation):
    from transbridge.ai_translator.translator import TranslationResult
    from transbridge.application.translation.completion_report import build_translation_report_snapshot

    completed = TranslationEntry("done", "done", "Hello", "你好", 1, "")
    failed = TranslationEntry("failed", "failed", "World", "", 0, "")
    polish = TranslationEntry("polish", "polish", "Welcome", "欢迎", 1, "")
    translations = (completed, failed) if has_failed_translation else (completed,)
    task = SourceTask("source", "Source", None, None, translations, (polish,))
    result = TranslationResult(
        success_count=1,
        failed_count=int(has_failed_translation),
        failed_entries=["failed: provider failed"] if has_failed_translation else [],
    )
    result.post_process_result = build_translation_report_snapshot(
        result, [completed], run_id=_request().run_id, cancelled=False
    )
    outcome = SourceOutcome(
        task, translation=result, failed_keys=tuple(entry.key for entry in task.entries), error="校对初始化失败"
    )
    record = build_task_record([outcome], _request(), state="failed")
    assert record["counts"] == {
        "successful": 1,
        "failed": int(has_failed_translation),
        "unprocessed": 1,
        "cancelled": 0,
    }
    rows = record["sources"][0]["snapshot"]["entries"]
    assert next(row for row in rows if row["entry_key"] == completed.identity.to_dict())["accepted"]
    assert "校对初始化失败" not in json.dumps([row["report_details"] for row in rows], ensure_ascii=False)


def test_store_metadata_update_does_not_rewrite_payload_and_listing_is_lightweight(tmp_path):
    store = TaskHistoryStore(tmp_path)
    record = build_task_record([_outcome()], _request(), state="preparing")
    path = store.save(record)
    before = path.read_bytes()
    store.update_metadata(record["run_id"], applied=True, saved=True, state="completed")
    assert path.read_bytes() == before
    summary = store.list_records()[0]
    assert "snapshot" not in summary["sources"][0]
    assert store.load(record["run_id"])["saved"] is True
    assert path.is_relative_to(tmp_path)


def test_failed_history_publication_preserves_previous_generation(tmp_path, monkeypatch):
    from transbridge.application.translation import task_history

    store = TaskHistoryStore(tmp_path)
    record = build_task_record([_outcome()], _request(), state="preparing")
    store.save(record)
    original = task_history.atomic_json

    def fail_metadata(path, value):
        if path.name == "metadata.json":
            raise OSError("disk full")
        original(path, value)

    monkeypatch.setattr(task_history, "atomic_json", fail_metadata)
    with pytest.raises(OSError, match="disk full"):
        store.save({**record, "state": "completed"})
    assert store.load(record["run_id"])["state"] == "preparing"
    assert len(list(tmp_path.glob("*/record-*.json"))) == 1


def test_polish_results_deepcopy_preserves_run_diagnostics():
    result = _PolishResults({}, diagnostics=(Diagnostic("CANCELLED", "Cancelled", category=ErrorCategory.CANCELLED),))
    copied = deepcopy(result)
    assert copied.diagnostics == result.diagnostics


def test_build_snapshot_never_exports_and_preserves_run_diagnostics(monkeypatch):
    from transbridge.ui.tools.ai_translator import reporting

    monkeypatch.setattr(reporting, "render_translation_report", lambda *_: pytest.fail("automatic export"))
    outcome = _outcome()
    outcome.diagnostics = (Diagnostic("RUN", "Run-level reason"),)
    snapshot = build_source_snapshot(outcome, _request())
    assert [item.code for item in snapshot.diagnostics] == ["RUN"]
    assert "Run-level reason" not in json.dumps(snapshot.candidates[0].to_dict())


def test_export_cancel_leaves_previous_file_intact(tmp_path, monkeypatch):
    from concurrent.futures import CancelledError
    import threading

    from transbridge.application.translation.postprocess_report import CsvReportRenderer
    from transbridge.ui.tools.ai_translator.reporting import export_snapshot

    snapshot = build_source_snapshot(_outcome(), _request())
    path = tmp_path / "report.csv"
    path.write_text("previous")
    cancel = threading.Event()
    render = CsvReportRenderer.render

    def cancelled_render(self, snapshot, **kwargs):
        result = render(self, snapshot, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(CsvReportRenderer, "render", cancelled_render)
    with pytest.raises(CancelledError):
        export_snapshot(snapshot, path, "csv", cancel_event=cancel)
    assert path.read_text() == "previous"
    assert not list(tmp_path.glob(".export-*"))
