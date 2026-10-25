from dataclasses import replace
import json

import pytest

from tests.ui.tools import test_unified_task_progress as support
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.ui.tools.ai_translator.task_entry_results import TaskEntryResults
from transbridge.ui.tools.ai_translator.task_result_recovery import (
    load_recovery_results,
    restore_recovery_results,
    save_recovery_results,
)

qapp = support.qapp
make_window = support.make_window


def prepared(session, tmp_path, *, decision="pending"):
    ledger = TaskEntryResults(session.tasks, "old-task")
    ledger.entries = {
        key: replace(
            row,
            status="succeeded",
            text=f"恢复译文 {row.source}",
            stage=2,
            decision=decision,
            attempt=1,
            report_details_json=json.dumps({"review_status": "needs_review", "review_reason": "歧义"}),
        )
        for key, row in ledger.entries.items()
    }
    store = TaskRecoveryStore(tmp_path / "recovery")
    record = {
        "task_id": "old-task",
        "sources": [
            {
                "key": task.key,
                "translate_keys": [entry.identity.to_dict() for entry in task.translate_entries],
                "polish_keys": [entry.identity.to_dict() for entry in task.polish_entries],
            }
            for task in session.tasks
        ],
    }
    save_recovery_results(store, record["task_id"], ledger)
    record["results"] = load_recovery_results(store, record)
    return record


@pytest.mark.parametrize("decision", ["pending", "accepted", "rejected"])
def test_restart_restores_results_decisions_and_review_evidence(tmp_path, decision):
    session = support._Session(polish=True)
    record = prepared(session, tmp_path, decision=decision)
    ledger, remaining, count = restore_recovery_results(
        TaskEntryResults(session.tasks, "new-task"), session.tasks, record
    )
    assert count == 2 and not remaining
    assert all(row.decision == decision and not row.applied for row in ledger.entries.values())
    assert all(row.report_details["review_reason"] == "歧义" for row in ledger.entries.values())
    assert len(ledger.index.pending_preview) == (2 if decision == "pending" else 0)


def test_saved_project_with_stale_manifest_is_not_applied_twice(tmp_path):
    session = support._Session()
    record = prepared(session, tmp_path, decision="accepted")
    for task in session.tasks:
        entry = task.translate_entries[0]
        entry.translation, entry.stage = f"恢复译文 {task.key}", 2
    ledger, remaining, count = restore_recovery_results(
        TaskEntryResults(session.tasks, "new-task"), session.tasks, record
    )
    assert count == 2 and not remaining
    assert len(ledger.applied_keys) == 2 and not ledger.ready_keys


def test_user_edits_are_never_overwritten(tmp_path):
    session = support._Session()
    record = prepared(session, tmp_path, decision="accepted")
    session.tasks[0].translate_entries[0].translation = "用户的新译文"
    with pytest.raises(ValueError, match="不能覆盖"):
        restore_recovery_results(TaskEntryResults(session.tasks, "new-task"), session.tasks, record)


def test_restore_pending_preview_never_starts_model_worker(qapp, make_window, tmp_path):
    window, session, _ = make_window(polish=True, preview=True)
    record = prepared(session, tmp_path)
    window.run.recovery.record = record
    seen = []
    window.run.preview_requested.connect(lambda: seen.append(True))
    window.run.result_flow.start(session.tasks)
    support._wait_until(lambda: bool(seen))
    assert window.run.state == "pending_confirmation"
    assert window.run.worker is None and session.commits == 0
    assert all(row.report_details["review_status"] == "needs_review" for row in window.run.entries.entries.values())


def test_restore_accepted_results_saves_without_model_worker(qapp, make_window, tmp_path):
    window, session, _ = make_window()
    window.run.recovery.record = prepared(session, tmp_path, decision="accepted")
    window.run.result_flow.start(session.tasks)
    support._wait_until(lambda: session.project_saved)
    support._wait_reports(qapp, window)
    assert window.run.worker is None and session.completed
    assert session.commits == 1 and len(session.applied_keys) == 2


def test_restore_rejected_results_keeps_user_choice(qapp, make_window, tmp_path):
    window, session, _ = make_window(polish=True, preview=True)
    window.run.recovery.record = prepared(session, tmp_path, decision="rejected")
    window.run.result_flow.start(session.tasks)
    support._wait_until(lambda: session.completed)
    support._wait_reports(qapp, window)
    assert window.run.worker is None and session.commits == 0
    assert all(row.decision == "rejected" for row in window.run.entries.entries.values())


def test_startup_status_write_cannot_discard_loaded_results(qapp, make_window, tmp_path, monkeypatch):
    window, session, _ = make_window(polish=True, preview=True)
    record = prepared(session, tmp_path)
    monkeypatch.setattr(window.run.recovery, "prepare", lambda **callbacks: callbacks["on_success"](record))
    write = window.run._record

    def publish_metadata_only():
        write()
        window.run.recovery.record = {"task_id": record["task_id"]}

    monkeypatch.setattr(window.run, "_record", publish_metadata_only)
    window.run.prepare()
    session.ready()
    support._wait_until(lambda: window.run.state == "pending_confirmation")
    assert window.run.worker is None
    assert len(window.run.entries.ready_keys) == 2


def test_retry_does_not_replay_old_results_over_current_decisions(qapp, make_window, tmp_path):
    window, session, _ = make_window()
    window.run.recovery.record = prepared(session, tmp_path)
    first = session.tasks[0].entries[0].identity
    window.run.entries.entries[first] = replace(
        window.run.entries.entries[first], status="succeeded", text="已拒绝的译文", decision="rejected"
    )
    window.run._attempt = 1
    window.run.result_flow.start((session.tasks[1],))
    support._wait_until(lambda: window.run.worker is not None)
    assert window.run.entries.entries[first].decision == "rejected"
    assert window.run.worker.tasks == (session.tasks[1],)
