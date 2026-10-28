"""Post-processing shares durable results, merge authority and cancellation evidence."""

from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest

from tests.smart_assistant.tools.test_translation_task_lifecycle import (
    current_texts,
    environment as environment_fixture,
    new_task,
    project as project_fixture,
)
from transbridge.application.contracts import Diagnostic, ErrorCategory, OperationOutcome
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation.postprocess import (
    PostProcessCandidate,
    PostProcessStageOutcome,
    ReportSnapshot,
)
from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.tools._postprocess_tool_runtime import PostprocessTaskResult, PostprocessToolRequest
from transbridge.smart_assistant.tools.postprocess_task_run import execute_task, launch_postprocess_task
from transbridge.smart_assistant.tools.translation_task_merge import merge_saved_results
from transbridge.smart_assistant.tools.translation_task_results import load_task_result, result_view

project = project_fixture
environment = environment_fixture


class Notifications:
    def update_progress(self, _task_id, data):
        self.progress = data

    def set_status(self, _task_id, state):
        self.state = state

    def notify_finished(self, _task_id, success, message, data):
        self.success, self.message, self.data = success, message, data


def report(task, *, outcome=OperationOutcome.COMPLETED, phases=("proofread",), reject_last=False):
    candidates = tuple(
        PostProcessCandidate(
            task.result_id,
            key,
            row.before.revision,
            row.before.original,
            row.before.translation,
            f"checked:{row.before.original}",
            2,
            phases=phases,
            accepted=not reject_last or index == 0,
        )
        for index, (key, row) in enumerate(task.ledger.entries.items())
    )
    return ReportSnapshot(
        "transbridge.postprocess-report.v1",
        task.result_id,
        outcome,
        len(candidates),
        sum(item.accepted for item in candidates),
        candidates,
        tuple(PostProcessStageOutcome(phase, candidates) for phase in phases),
        (),
    )


def run(task, snapshot, monkeypatch, *, before_return=lambda: None, expected=("proofread",)):
    manager = Notifications()
    guard = ImmediateCommitGuard("runtime")
    handle = SimpleNamespace(
        stop_event=threading.Event(),
        pause_event=threading.Event(),
        execution=SimpleNamespace(ref=SimpleNamespace(run_id="runtime"), commit=guard.commit),
    )

    def execute(request, **options):
        assert options["collection"] is task.task.collection
        assert options["collection"] is not task.app.collection
        assert request.entries == task.task.postprocess_entries
        assert options["ctx"].esp_path == task.esp_path
        before_return()
        return PostprocessTaskResult(
            {"outcome": snapshot.outcome.value},
            {"outcome": snapshot.outcome.value},
            snapshot.outcome is OperationOutcome.CANCELLED,
            False,
            snapshot,
        )

    monkeypatch.setattr("transbridge.smart_assistant.tools.postprocess_task_run.execute_postprocess_task", execute)
    execute_task(
        task,
        SimpleNamespace(entries=task.task.postprocess_entries, stages=expected),
        task.store.root / "reports",
        lambda _data, _context: None,
        manager,
        "runtime",
        handle,
    )
    return manager


def test_postprocess_results_preserve_concurrent_manual_edit_and_remerge_without_model(environment, monkeypatch):
    app, services, context = environment
    task = new_task(environment, mode="postprocess")
    snapshot = report(task)
    key = next(iter(task.ledger.entries))

    def manual_edit():
        assert services.gui_project_commands.update_entry(key, context, translation="manual", stage=5).is_success
        app.esp_path = "switched.esp"

    manager = run(task, snapshot, monkeypatch, before_return=manual_edit)
    assert manager.success
    assert manager.data["merge"]["applied_count"] == 1
    assert manager.data["merge"]["kept_current_count"] == 1
    assert current_texts(environment)[key] == "manual"
    view = result_view(app, task.result_id)
    assert view["mode"] == "postprocess" and len(view["results"]) == 2
    assert {row["action"] for row in view["results"]} == {"postprocess"}
    store, record, payload = load_task_result(app, task.result_id)
    summary, _warning = merge_saved_results(app, store, record, payload, policy="prefer_incoming")
    assert summary["applied_count"] == 1
    assert current_texts(environment)[key].startswith("checked:")


def test_cancelled_complete_entries_remain_recoverable_without_automatic_merge(environment, monkeypatch):
    app, _services, _context = environment
    task = new_task(environment, mode="postprocess")
    manager = run(task, report(task, outcome=OperationOutcome.CANCELLED, reject_last=True), monkeypatch)
    assert manager.data["state"] == "cancelled" and manager.data["success_count"] == 1
    assert set(current_texts(environment).values()) == {""}
    store, record, payload = load_task_result(app, task.result_id)
    assert len(payload["rows"]) == 1
    assert merge_saved_results(app, store, record, payload)[0]["applied_count"] == 1


@pytest.mark.parametrize("outcome", [OperationOutcome.CANCELLED, OperationOutcome.FAILED])
def test_unfinished_strict_stage_prefix_cannot_be_merged(environment, monkeypatch, outcome):
    app, _services, _context = environment
    task = new_task(environment, mode="postprocess")
    manager = run(
        task, report(task, outcome=outcome, phases=("refine",)), monkeypatch, expected=("refinement", "arbitration")
    )
    assert manager.data["success_count"] == 0
    assert result_view(app, task.result_id)["results"] == []
    assert set(current_texts(environment).values()) == {""}


def test_cancelled_initial_candidates_are_not_successful(environment, monkeypatch):
    task = new_task(environment, mode="postprocess")
    manager = run(task, report(task, outcome=OperationOutcome.CANCELLED, phases=()), monkeypatch)
    assert manager.data["success_count"] == 0
    assert set(current_texts(environment).values()) == {""}


def test_last_stage_transport_failure_cannot_publish_retained_prefix(environment, monkeypatch):
    task = new_task(environment, mode="postprocess")
    snapshot = report(task, outcome=OperationOutcome.PARTIAL, phases=("refine",))
    diagnostic = Diagnostic("MODEL_FAILED", "last stage unavailable", category=ErrorCategory.EXTERNAL)
    snapshot = replace(
        snapshot,
        stage_outcomes=(
            *snapshot.stage_outcomes,
            PostProcessStageOutcome("arbitrate", snapshot.candidates, (diagnostic,)),
        ),
    )
    manager = run(task, snapshot, monkeypatch, expected=("refinement", "arbitration"))
    assert manager.data["success_count"] == 0
    assert set(current_texts(environment).values()) == {""}


def test_keyed_last_stage_failure_preserves_other_validated_candidates(environment, monkeypatch):
    task = new_task(environment, mode="postprocess")
    snapshot = report(task, outcome=OperationOutcome.PARTIAL, phases=("refine",))
    key = snapshot.candidates[0].entry_key
    diagnostic = Diagnostic(
        "MODEL_FAILED", "one entry failed", category=ErrorCategory.EXTERNAL, details=(("entry_key", key.to_dict()),)
    )
    snapshot = replace(
        snapshot,
        stage_outcomes=(
            *snapshot.stage_outcomes,
            PostProcessStageOutcome("arbitrate", snapshot.candidates, (diagnostic,)),
        ),
    )
    manager = run(task, snapshot, monkeypatch, expected=("refinement", "arbitration"))
    assert manager.data["success_count"] == 1 and manager.data["merge"]["applied_count"] == 1
    assert current_texts(environment)[key] == ""


def test_postprocess_save_failure_keeps_complete_candidates_and_blocks_merge(environment, monkeypatch):
    app, _services, _context = environment
    task = new_task(environment, mode="postprocess")
    save = task.store.save_results
    monkeypatch.setattr(task.store, "save_results", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    manager = run(task, report(task), monkeypatch)
    assert not manager.success
    view = result_view(app, task.result_id)
    assert view["state"] == "persistence_failed"
    assert {item["status"] for item in view["execution"]} == {"succeeded"}
    assert set(current_texts(environment).values()) == {""}
    monkeypatch.setattr(task.store, "save_results", save)
    store, record, payload = load_task_result(app, task.result_id)
    assert len(payload["rows"]) == 2
    assert merge_saved_results(app, store, record, payload)[0]["applied_count"] == 2


def test_postprocess_report_rejects_other_run_and_duplicate_entries(environment):
    task = new_task(environment, mode="postprocess")
    snapshot = report(task)
    with pytest.raises(ValueError, match="当前任务"):
        task.ledger.ingest_postprocess(replace(snapshot, run_id="other"))
    with pytest.raises(ValueError, match="重复"):
        task.ledger.ingest_postprocess(replace(snapshot, candidates=(snapshot.candidates[0],) * 2))


def test_quality_report_is_bound_to_owner_session_and_version(environment):
    from transbridge.smart_assistant.tools.tool_proofreader import get_last_report, set_last_report
    from transbridge.smart_assistant.tools.types import ExecutionContext

    app, _services, context = environment
    caller = ExecutionContext(app_context=app, request_context=context)
    set_last_report({"secret": "caller report"}, caller)
    for other_context in (replace(context, session_id="other"), replace(context, owner_id="other")):
        assert get_last_report(ExecutionContext(app_context=app, request_context=other_context)) is None
    app.active_version_identity = app.active_version_identity[0], "other-variant"
    assert get_last_report(app) is None
    assert get_last_report(caller) == {"secret": "caller report"}
    changed = get_last_report(caller)
    changed["secret"] = "changed"
    assert get_last_report(caller)["secret"] == "caller report"


def test_postprocess_start_failure_finishes_runtime_and_retains_queryable_failure(environment, monkeypatch):
    app, _services, _context = environment
    request = PostprocessToolRequest(
        tuple(app.collection),
        LLMConfig(api_key="fixture", term_priority=[]),
        "builtin:polish",
        None,
        "polish",
        "proofread",
        ("proofread",),
        "all",
        "medium",
        {},
        SimpleNamespace(snapshot_ref=None),
    )
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.postprocess_task_run.resolve_postprocess_request", lambda *_args: request
    )
    manager = Notifications()
    manager.register = lambda **_options: "runtime"
    manager.get_handle = lambda _task_id: None
    manager.start_thread = lambda *_args: (_ for _ in ()).throw(OSError("worker unavailable"))
    result = launch_postprocess_task(
        {},
        app,
        report_directory=lambda _context: pytest.fail("report directory I/O must run in the worker"),
        report_callback=lambda *_args: None,
        manager=manager,
    )
    assert not result.success and not manager.success
    view = result_view(app, result.data["result_id"])
    assert view["state"] == "failed" and view["results"] == []
    assert "worker unavailable" in view["error"]
