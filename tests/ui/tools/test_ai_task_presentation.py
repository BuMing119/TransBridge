from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from tests.ui.tools import test_ai_partial_task as fixtures
from tests.ui.tools.test_ai_partial_task import _outcome, _wait
from transbridge.ui.tools.ai_translator.task_run_presentation import concise_event, source_label, summarize_entries

make_task = fixtures.make_task
qapp = fixtures.qapp


def _row(status="succeeded", *, reason="", stage=3, decision="pending", applied=False):
    return SimpleNamespace(
        source="source", status=status, reason=reason, stage=stage, decision=decision, applied=applied
    )


def test_summary_counts_final_entries_and_review_is_success_subset():
    entries = {
        "review": _row(reason="[PROOFREAD_SYNTAX_REVIEW_REQUIRED] 标记需检查", stage=2),
        "existing-stage2": _row(stage=2),
        "recovered": _row(reason="[PROOFREAD_RECOVERY_SUCCEEDED] recovered", applied=True),
        "rejected": _row(decision="rejected"),
        "failed": _row("failed", reason="[PROOFREAD_RESPONSE_SCHEMA_INVALID] 未返回译文；未返回译文"),
        "failed2": _row("failed", reason="[PROOFREAD_RESPONSE_SCHEMA_INVALID] 未返回译文"),
        "unstarted": _row("not_started"),
        "cancelled": _row("cancelled"),
    }
    summary = summarize_entries(entries)
    assert (summary.succeeded, summary.review, summary.failed, summary.unprocessed, summary.cancelled) == (
        4,
        2,
        2,
        1,
        1,
    )
    assert summary.reasons == (("未返回译文", 2),)
    assert (summary.applied, summary.rejected, summary.unapplied) == (1, 1, 2)
    assert "其中有疑问 2 条" in summary.text()
    assert "未应用 2 条" in summary.text(cancelled=True)
    assert "PROOFREAD" not in summary.text()


def test_events_drop_internal_details_and_use_source_label():
    assert concise_event("开始校对，共 8277 条") == "开始校对，共 8277 条"
    assert concise_event("2 条术语修复请求失败，保留原译文") == "2 条术语修复请求失败，保留原译文"
    assert concise_event("正在拆分重试 1 条未完成条目") == "正在拆分重试 1 条未完成条目"
    assert concise_event("已恢复 1 条") == "已恢复 1 条"
    assert concise_event("[PROOFREAD_LLM_CALL_FAILED] error") is None
    assert concise_event(r"正在写入 D:\private\report.json") is None
    assert concise_event('{"entry_key": "internal"}') is None
    assert source_label((SimpleNamespace(key="internal", label=r"D:\Mods\Remi"),), "internal") == "Remi"


def test_live_events_are_plain_text_and_pause_changes_are_visible(make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    worker.log.emit("first", "开始校对，共 4 条")
    worker.log.emit("first", "[PROOFREAD_LLM_CALL_FAILED] D:\\private\\trace")
    window.run._pause_changed("pausing")
    window.run._pause_changed("paused")
    window.run._pause_changed("running")
    window.run.logged.emit("开始 <b>校对</b>")
    text = window.logs.toPlainText()
    assert "First · 开始校对，共 4 条" in text
    assert "PROOFREAD" not in text and "private" not in text
    assert "正在暂停" in text and "已暂停" in text and "已继续" in text
    assert "<b>校对</b>" in text


def test_summary_refreshes_after_apply_rejection_and_recovery(make_task, qapp):
    window, _, _ = make_task()
    window.run.worker.publish(_outcome(window.run.worker))
    _wait(qapp, lambda: not window.run.busy)
    assert "成功 3 条" in window.result_summary.text()
    assert "未完成 1 条" in window.result_summary.text()
    keys = window.run.entries.entries
    reject = next(key for key, row in keys.items() if row.before.id == "rejected")
    review = next(key for key, row in keys.items() if row.before.id == "polished")
    keys[review] = replace(keys[review], stage=2, reason="[PROOFREAD_SYNTAX_REVIEW_REQUIRED] 检查标记")
    window.run.entries.decide({key: key != reject for key in window.run.entries.ready_keys})
    window.run.apply_results()
    _wait(qapp, lambda: not window.run.busy)
    text = window.result_summary.text()
    assert "其中有疑问 1 条" in text and "已应用 2 条" in text and "未采纳 1 条" in text
    window.run.retry()
    window.run.worker.publish(_outcome(window.run.worker, failed=False))
    _wait(qapp, lambda: not window.run.busy)
    assert "成功 4 条" in window.result_summary.text() and "未完成 0 条" in window.result_summary.text()
    assert "未完成原因" not in window.result_summary.text()


def test_cancelled_summary_does_not_claim_pending_application(make_task, qapp):
    window, _, _ = make_task()
    window.run.cancel()
    window.run.worker.publish(_outcome(window.run.worker, failed=False))
    _wait(qapp, lambda: not window.run.busy)
    assert "已应用 0 条" in window.result_summary.text()
    assert "待应用" not in window.result_summary.text()
