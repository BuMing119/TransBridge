"""Unified request controls change worker state without crossing scope or runs."""

from dataclasses import replace
from threading import Event

import pytest

from tests.smart_assistant.test_request_execution import register
from transbridge.application.assistant_requests.models import ExecutionDispatch
from transbridge.application.tasks import JobState
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.request_task_control import control_request_tasks
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.smart_assistant.tools.types import ToolResult

pytest_plugins = ["tests.smart_assistant.test_request_execution"]


@pytest.fixture
def running_job(setup):
    service, context, gate, ctx, manager = setup
    finish = Event()
    started = Event()

    def tool(_args, execution_context):
        task_id = manager.register(metadata=task_metadata(execution_context, {}))

        def work():
            started.set()
            assert finish.wait(10)

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    assert result.success, result
    assert started.wait(3)
    task_id = result.data["task_id"]
    handle = manager.get_handle(task_id)
    try:
        yield service, context, gate, manager, task_id, handle, finish
    finally:
        finish.set()
        handle._thread.join(3)
        assert not handle._thread.is_alive()


def _command(data, action):
    service, context, gate, manager, *_ = data
    request = gate.current_request()
    updated = service.command(context, request.request_id, action, request.revision)
    return control_request_tasks(manager, service, context, updated, action)


def test_unified_pause_resume_changes_runtime_worker_signal_and_saved_wait(running_job):
    service, context, gate, manager, task_id, handle, _ = running_job
    assert _command(running_job, "pause") == (task_id,)
    assert manager.get_status(task_id)["status"] == "paused"
    assert not handle.pause_event.is_set()
    assert gate.current_request().pause_reasons == ("user_paused",)
    assert "user_job_control" in gate.current_request().items[0].waiting_reasons
    assert _command(running_job, "resume") == (task_id,)
    assert manager.runtime.get(handle.execution.ref, handle.execution.owner).state == JobState.RUNNING
    assert handle.pause_event.is_set()
    assert not gate.current_request().pause_reasons
    assert "user_job_control" not in gate.current_request().items[0].waiting_reasons
    operations = [event["operation"] for event in service.state(context)["lifecycle_events"]]
    assert "task.pause" in operations and "task.resume" in operations


@pytest.mark.parametrize("field", ["owner_id", "session_id", "project_id", "variant_id"])
def test_other_scope_is_not_controlled_even_with_matching_request_metadata(running_job, field):
    service, context, gate, manager, task_id, handle, _ = running_job
    other = replace(context, **{field: "other"})
    request = replace(gate.current_request(), session_id=other.session_id)
    assert control_request_tasks(manager, service, other, request, "pause") == ()
    assert manager.get_status(task_id)["status"] == "running"
    assert handle.pause_event.is_set()


def test_dispatch_fallback_requires_exact_run_and_leaves_unrelated_tasks_running(running_job):
    service, context, gate, manager, task_id, handle, _ = running_job
    unrelated_id = manager.register(metadata={"owner_id": context.owner_id, "session_id": context.session_id})
    unrelated = manager.get_handle(unrelated_id)
    # The same session and job identity alone cannot authorize a different run.
    current = gate.current_request()
    dispatch = ExecutionDispatch("fallback", current.effects[0].execution, task_id, handle.execution.ref.run_id)
    request = replace(current, request_id="fallback-only", dispatches=(dispatch,))
    wrong = replace(request, dispatches=tuple(replace(d, run_id="old-run") for d in request.dispatches))
    assert control_request_tasks(manager, service, context, wrong, "pause") == ()
    assert control_request_tasks(manager, service, context, request, "pause") == (task_id,)
    assert not handle.pause_event.is_set()
    assert unrelated.pause_event.is_set()
    assert manager.get_status(unrelated_id)["status"] == "running"


def test_changed_run_between_projection_and_action_is_rejected(running_job, monkeypatch):
    service, context, gate, manager, task_id, handle, _ = running_job
    original = manager.get_status
    reads = 0

    def changed(task):
        nonlocal reads
        status = original(task)
        if task == task_id:
            reads += 1
            if reads > 1:
                status["run_id"] = "replacement-run"
        return status

    monkeypatch.setattr(manager, "get_status", changed)
    with pytest.raises(RuntimeError, match="运行代次.*改变") as failure:
        control_request_tasks(manager, service, context, gate.current_request(), "pause")
    assert failure.value.__cause__ is not None
    assert manager.runtime.get(handle.execution.ref, handle.execution.owner).state == JobState.RUNNING


def test_terminal_race_is_ignored_without_restarting_finished_work(running_job, monkeypatch):
    service, context, gate, manager, task_id, handle, _ = running_job
    original = manager.runtime.pause

    def complete_first(ref, owner, **kwargs):
        manager.runtime.complete(ref, owner)
        return original(ref, owner, **kwargs)

    monkeypatch.setattr(manager.runtime, "pause", complete_first)
    assert control_request_tasks(manager, service, context, gate.current_request(), "pause") == ()
    assert manager.get_status(task_id)["status"] == "completed"
    assert control_request_tasks(manager, service, context, gate.current_request(), "resume") == ()


def test_real_control_failure_surfaces_context_and_preserves_cause(running_job, monkeypatch):
    _service, _context, gate, manager, task_id, handle, _ = running_job
    cause = OSError("runtime unavailable")

    def fail(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(manager.runtime, "pause", fail)
    with pytest.raises(RuntimeError, match="后台任务暂停未全部完成") as failure:
        _command(running_job, "pause")
    assert task_id in str(failure.value)
    assert failure.value.__cause__ is cause
    assert gate.current_request().pause_reasons == ("user_paused",)
    assert handle.pause_event.is_set()


def test_noop_and_wrong_session_never_dispatch_control(running_job):
    service, context, gate, manager, task_id, _, _ = running_job
    assert _command(running_job, "pause") == (task_id,)
    assert control_request_tasks(manager, service, context, gate.current_request(), "pause") == ()
    with pytest.raises(ValueError, match="不属于当前会话"):
        control_request_tasks(manager, service, replace(context, session_id="new"), gate.current_request(), "resume")
