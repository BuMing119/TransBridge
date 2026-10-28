"""Canonical review evidence survives mixed-task ledger, history and recovery paths."""

from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

from openpyxl import load_workbook

from tests.ui.tools import test_ai_partial_task as support
from tests.ui.tools.test_task_recovery_binding import setup_binding
from transbridge.application.contracts import OperationOutcome
from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot
from transbridge.application.translation.postprocess_report import ExcelReportRenderer
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_history import TaskHistoryStore, restore_report_snapshot

qapp = support.qapp
make_task = support.make_task


def review_details(status="term_suspect"):
    return {
        "review_status": status,
        "review_reason": "卫兵语境与参考译法不同，保留当前译文",
        "review_contract_version": 1,
        "review_reference_terms": {"Guards": "瓜尔", "Secret": "秘密"},
        "review_before_text": "卫兵",
        "detected_issues": [{"issue_type": "term_mismatch", "term": "Guards", "standard_translation": "瓜尔"}],
        "result_status": "failed",  # A report projection cannot override actual execution status.
    }


def with_review_snapshot(outcome, run_id):
    candidates = tuple(
        PostProcessCandidate(
            run_id=run_id,
            entry_key=entry.identity,
            before_revision=entry.revision,
            original=entry.original,
            before_text="卫兵",
            text=f"新 {entry.id}",
            stage=2,
            report_details=tuple(review_details().items()),
        )
        for entry in outcome.task.translate_entries
    )
    outcome.translation.post_process_result = ReportSnapshot(
        schema="transbridge.postprocess-report.v1",
        run_id=run_id,
        outcome=OperationOutcome.COMPLETED,
        input_count=len(candidates),
        accepted_count=len(candidates),
        candidates=candidates,
        stage_outcomes=(),
        diagnostics=(),
    )
    for result in outcome.polish.values():
        for key, value in review_details("needs_review").items():
            setattr(result, key, value)
        result.original_translation = "校对前的随从"
    return outcome


def test_mixed_completion_history_and_excel_keep_advisory_flags_without_failures(qapp, make_task):
    window, _, _ = make_task(preview_enabled=False)
    worker = window.run.worker
    outcome = with_review_snapshot(support._outcome(worker, failed=False), window.request.run_id)
    worker.publish(outcome)
    support._settle(window)
    assert window.session.completed
    assert not window.run.entries.failed_keys
    record = TaskHistoryStore(window.session.history_dir).load(window.request.run_id)
    snapshot = restore_report_snapshot(record["sources"][0]["snapshot"])
    assert record["counts"]["successful"] == 4 and record["counts"]["failed"] == 0
    for candidate in snapshot.candidates:
        details = dict(candidate.report_details)
        assert details["result_status"] == "applied"
        assert details["review_status"] in {"term_suspect", "needs_review"}
        assert details["detected_issues"][0]["standard_translation"] == "瓜尔"
        assert details["review_reference_terms"]["Secret"] == "秘密"
    workbook = load_workbook(BytesIO(ExcelReportRenderer().render(snapshot).content))
    rows = list(workbook["校对标注"].values)[1:]
    assert len(rows) == 4
    assert {row[5] for row in rows} == {"疑似参考术语错误", "待复核"}
    # The explicit latest-review input takes precedence over the original polish input.
    assert {row[3] for row in rows} == {"卫兵"}


def test_ingestion_detaches_nested_evidence_and_retry_drops_stale_review(make_task):
    window, _, _ = make_task()
    outcome = with_review_snapshot(support._outcome(window.run.worker, failed=False), window.request.run_id)
    ledger = TaskEntryResults((outcome.task,), "immutable-evidence")
    keys = set(ledger.entries)
    ledger.ingest((outcome,), keys)
    frozen = ledger.freeze()
    key = outcome.task.translate_entries[0].identity
    source = dict(outcome.translation.post_process_result.candidates[0].report_details)
    source["detected_issues"][0]["term"] = "mutated externally"
    exposed = ledger.entries[key].report_details
    exposed["detected_issues"][0]["term"] = "mutated by consumer"
    assert frozen.entries[key].report_details["detected_issues"][0]["term"] == "Guards"
    retried = support._outcome(window.run.worker, failed=False)
    ledger.ingest((retried,), (key,))
    assert "review_status" not in ledger.entries[key].report_details
    assert frozen.entries[key].report_details["review_status"] == "term_suspect"
    assert ledger.entries[key].attempt == 2


def test_translation_entry_outcome_carries_review_without_postprocess_snapshot(make_task):
    window, _, _ = make_task()
    outcome = support._outcome(window.run.worker, failed=False)
    key = outcome.task.translate_entries[0].identity
    outcome.translation.entry_outcomes[key] = replace(
        outcome.translation.entry_outcomes[key], review_details=tuple(review_details("retained").items())
    )
    ledger = TaskEntryResults((outcome.task,), "fallback-evidence")
    ledger.ingest((outcome,), (key,))
    row = ledger.snapshot(outcome.task.key).candidates[0]
    details = dict(row.report_details)
    assert row.accepted and details["review_status"] == "retained"
    assert details["processing_status"] == "completed" and details["result_status"] == "pending"


def test_saved_recovery_keeps_report_facing_evidence_without_reference_map(tmp_path):
    binding, request, session, _ = setup_binding(tmp_path)
    ledger = TaskEntryResults(session.tasks, request.run_id)
    task = session.tasks[0]
    entry = task.polish_entries[0]
    result = SimpleNamespace(
        **review_details("needs_review"),
        original_translation=entry.translation,
        polished_translation="卫兵",
        accepted=True,
        confidence=0.9,
        processing_status="completed",
    )
    outcome = SimpleNamespace(task=task, translation=None, polish={entry.id: result}, error="")
    ledger.ingest((outcome,), (entry.identity,))
    ledger.mark_applied((entry.identity,))
    session.project_saved = True
    run = SimpleNamespace(state="completed", user_cancelled=False, shutting_down=False, entries=ledger)
    binding.update(run)
    saved = binding.store.load(request.run_id)["applied_entries"][0]
    assert saved["report_details"]["review_status"] == "needs_review"
    assert saved["report_details"]["detected_issues"][0]["term"] == "Guards"
    assert "review_reference_terms" not in saved["report_details"]
