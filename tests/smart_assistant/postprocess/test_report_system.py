"""Canonical smart-assistant report publication and history tests."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from transbridge.smart_assistant.tools.base import ExecutionContext


@pytest.fixture
def report_context(tmp_path):
    from transbridge.application.contracts import RequestContext
    from transbridge.persistence.task_store import TaskStore

    app = SimpleNamespace(
        active_version_identity=("project", "version"),
        task_store=TaskStore(tmp_path / "task-store"),
        runtime_context=RequestContext("my-owner", session_id="my-session"),
        project_commands=SimpleNamespace(result_receipt=lambda *_: None, project_exists=lambda _: True),
    )
    return ExecutionContext(app_context=app)


def record_task(ctx, run_id, *, candidate=False, **fields):
    store = ctx.app_context.task_store
    recovery = store.recovery()
    record = {
        "task_id": run_id,
        "owner_id": "my-owner",
        "session_id": "my-session",
        "project_id": "project",
        "variant_id": "version",
        "origin": "smart-assistant",
        "mode": "polish",
        "config_digest": "test",
        "state": "completed",
        "created_at": "2026-11-09T01:00:00+00:00",
        "sources": [],
        "results_only": True,
        **fields,
    }
    recovery.create(record)
    if candidate:
        recovery.save_results(
            run_id,
            [
                {
                    "entry_key": {"namespace": "source", "local_key": "hello"},
                    "source": "Example.esp",
                    "action": "polish",
                    "before": {"original": "Hello", "translation": "你好", "stage": 1, "context": "", "revision": 0},
                    "status": "succeeded",
                    "text": "您好",
                    "stage": 1,
                    "reason": "",
                    "decision": "accepted",
                    "applied": False,
                    "attempt": 1,
                    "report_details": {"review_status": "needs_review"},
                }
            ],
        )
    return record


@pytest.mark.parametrize("stored_snapshot", [True, False])
def test_latest_quality_report_matches_desktop_and_is_scoped(report_context, stored_snapshot):
    from transbridge.application.tasks.history_service import TaskHistoryService
    from transbridge.application.translation.task_history import restore_report_snapshot
    from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report
    from transbridge.ui.tools.ai_translator.reporting import report_overview

    ctx = report_context
    record_task(ctx, "mine", candidate=True, created_at="2026-11-08T01:00:00+00:00")
    app = ctx.app_context
    service = TaskHistoryService(app.task_store, app.project_commands)
    if stored_snapshot:
        report = service.load("mine")
        report["sources"][0]["snapshot"]["issues"] = 3
        app.task_store.history(owner_id="my-owner", session_id="my-session").save(report)

    for name, changed in (
        ("owner", {"owner_id": "other"}),
        ("session", {"session_id": None}),
        ("project", {"project_id": "other-project"}),
        ("variant", {"variant_id": "other-version"}),
        ("ongoing", {"state": "running"}),
        ("download", {"mode": "download"}),
        ("origin", {"origin": "desktop"}),
    ):
        record_task(ctx, name, candidate=True, **changed)
    result = _tool_get_quality_report({}, ctx)
    assert result.success, result.message
    source = service.load("mine")["sources"][0]
    snapshot = restore_report_snapshot(source["snapshot"])
    assert result.data["result_id"] == "mine"
    assert result.data["reports"] == [
        {
            "source": source["label"],
            "input": 1,
            "accepted": 1,
            "issues": 3 if stored_snapshot else 0,
            "failures": 0,
            **asdict(report_overview(snapshot)),
        }
    ]
    assert result.data["reports"][0]["needs_review"] == 1


@pytest.mark.parametrize(("state", "with_result"), [("failed", False), ("cancelled", True)])
def test_latest_ended_task_reports_its_own_state_and_saved_candidates(report_context, state, with_result):
    from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

    ctx = report_context
    assert _tool_get_quality_report({}, ctx).data == {"reports": []}
    record_task(ctx, "old", candidate=True, created_at="2026-11-08T01:00:00+00:00")
    record_task(ctx, "current", state=state, error="执行已结束", candidate=with_result)
    result = _tool_get_quality_report({}, ctx)
    assert result.success, result.message
    assert result.data["result_id"] == "current" and result.data["state"] == state
    assert result.data["error"] == "执行已结束"
    assert len(result.data["reports"]) == int(with_result)


def test_switching_version_rejects_pending_report_request(report_context):
    from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

    record_task(report_context, "mine", candidate=True)
    report_context.app_context.active_version_identity = ("project", "other-version")
    result = _tool_get_quality_report({}, report_context)
    assert not result.success and result.error_code == "TASK_RESULT_SCOPE_REQUIRED"
