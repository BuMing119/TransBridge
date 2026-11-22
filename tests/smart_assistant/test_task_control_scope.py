from __future__ import annotations

from dataclasses import asdict, replace
import threading
from types import SimpleNamespace

import pytest

from tests.application.terminology_sync.test_service import _Contexts, _plan
from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import (
    JobEventType,
    JobSpec,
    JobState,
    RuntimeTaskProjection,
    TaskAccessError,
    TaskEventFilter,
)
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.service import TerminologySyncApplicationService, owner_from_context
from transbridge.application.terminology_sync.task_adapter import TerminologySyncTaskEntrypoint
from transbridge.application.terminology_sync.use_case import TerminologySyncPlanningUseCase
from transbridge.smart_assistant.tools.base import ExecutionContext
from transbridge.smart_assistant.tools.task_control import control_tasks, get_scoped_task_status
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.smart_assistant.tools.tool_terminology_sync import _tool_execute
from transbridge.smart_assistant.tools.tool_translator import _tool_get_task_status, _tool_stop_task
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter
from transbridge.ui.tools.terminology.sync_task_adapter import TerminologySyncTaskAdapter


@pytest.fixture
def manager():
    TaskManager.reset()
    value = TaskManager()
    yield value
    TaskManager.reset()


def context(session="session-1"):
    return SimpleNamespace(owner_id="assistant", session_id=session)


def register(manager, session="session-1"):
    return manager.register(metadata={"owner_id": "assistant", "session_id": session})


def test_ambiguous_stop_leaves_every_task_running(manager):
    first, second = register(manager), register(manager)
    result = control_tasks({}, context(), manager)
    assert not result.success
    assert set(result.data["candidate_task_ids"]) == {first, second}
    assert all(manager.get_status(tid)["status"] == "running" for tid in (first, second))


def test_unique_stop_ignores_other_session_and_repeated_stop_is_idempotent(manager):
    own, other = register(manager), register(manager, "session-2")
    first = control_tasks({}, context(), manager)
    repeated = control_tasks({"task_id": own}, context(), manager)
    assert first.success and repeated.success
    assert first.data["status"] == repeated.data["status"] == "cancelled"
    assert manager.get_status(other)["status"] == "running"


def test_explicit_all_stays_within_scope(manager):
    own = [register(manager), register(manager)]
    other = register(manager, "session-2")
    result = control_tasks({"all_tasks": True}, context(), manager)
    assert result.success and set(result.data["affected_task_ids"]) == set(own)
    assert manager.get_status(other)["status"] == "running"


@pytest.mark.parametrize("args", [{"task_id": "other"}, {"task_id": "other", "action": "pause"}])
def test_explicit_foreign_task_is_not_authorized(manager, args):
    other = register(manager, "session-2")
    result = control_tasks({**args, "task_id": other}, context(), manager)
    assert not result.success
    assert manager.get_status(other)["status"] == "running"
    assert not get_scoped_task_status({"task_id": other}, context(), manager).success


def test_missing_scope_does_not_grant_access_to_scoped_tasks(manager):
    task = register(manager)
    assert not control_tasks({"task_id": task}, SimpleNamespace(), manager).success
    assert get_scoped_task_status({}, SimpleNamespace(), manager).data["tasks"] == []


def test_active_worker_reports_cancelling_until_worker_exits(manager):
    task = register(manager)
    entered, release = threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert release.wait(5)

    thread = manager.start_thread(task, worker)
    try:
        assert entered.wait(2)
        result = control_tasks({"task_id": task}, context(), manager)
        repeated = control_tasks({"task_id": task}, context(), manager)
        assert result.success and repeated.success
        assert result.data["status"] == repeated.data["status"] == "cancelling"
        assert "仍在取消中" in result.message
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert manager.get_status(task)["status"] == "cancelled"


def test_terminal_notification_is_once_and_captures_original_identity(manager):
    queue, received = [], []
    TaskManager.set_main_thread_dispatcher(queue.append)
    manager.on_terminal(lambda *args: received.append(args))
    task = register(manager)
    run = manager.get_status(task)["run_id"]
    manager.cancel(task)
    manager.cancel(task)
    manager.notify_failed(task, "duplicate")
    manager.cleanup(task)
    for callback in queue:
        callback()
    assert len(received) == 1
    event = received[0][0]
    assert event["run_id"] == run
    assert event["status"] == "cancelled"
    assert event["owner"]["session_id"] == "session-1"


def register_child(manager, parent, session="session-1"):
    request = SimpleNamespace(
        owner_id="assistant", session_id=session, metadata=(("parent_task_id", parent), ("unrelated", "private"))
    )
    metadata = task_metadata(SimpleNamespace(request_context=request), {"job_type": "translation"})
    assert metadata["parent_task_id"] == parent
    assert "unrelated" not in metadata
    return manager.register(metadata=metadata)


def test_omitted_stop_selects_same_scope_active_parent_over_child(manager):
    parent = register(manager)
    child = register_child(manager, parent)
    foreign = register(manager, "session-2")
    result = control_tasks({}, context(), manager)
    assert result.success
    assert result.data["affected_task_ids"] == [parent]
    assert manager.get_status(child)["metadata"]["parent_task_id"] == parent
    assert manager.get_status(foreign)["status"] == "running"


def test_explicit_child_stop_leaves_parent_running(manager):
    parent = register(manager)
    child = register_child(manager, parent)
    result = control_tasks({"task_id": child}, context(), manager)
    assert result.success
    assert result.data["affected_task_ids"] == [child]
    assert manager.get_status(parent)["status"] == "running"


@pytest.mark.parametrize("parent_state", ["foreign", "completed", "missing"])
def test_child_remains_default_candidate_without_same_scope_active_parent(manager, parent_state):
    parent = register(manager, "session-2" if parent_state == "foreign" else "session-1")
    if parent_state == "completed":
        manager.set_status(parent, "completed")
    elif parent_state == "missing":
        manager.cleanup(parent)
    child = register_child(manager, parent)
    result = control_tasks({}, context(), manager)
    assert result.success
    assert result.data["affected_task_ids"] == [child]
    if parent_state == "foreign":
        assert manager.get_status(parent)["status"] == "running"


def test_two_parents_with_children_still_require_disambiguation(manager):
    parents = [register(manager), register(manager)]
    children = [register_child(manager, parent) for parent in parents]
    result = control_tasks({}, context(), manager)
    assert not result.success
    assert set(result.data["candidate_task_ids"]) == set(parents)
    assert all(manager.get_status(tid)["status"] == "running" for tid in parents + children)


def test_explicit_all_includes_parent_and_child_in_current_scope(manager):
    parent = register(manager)
    child = register_child(manager, parent)
    other = register_child(manager, parent, "session-2")
    result = control_tasks({"all_tasks": True}, context(), manager)
    assert result.success
    assert set(result.data["affected_task_ids"]) == {parent, child}
    assert manager.get_status(other)["status"] == "running"


def test_cleaned_bridge_job_does_not_reappear_as_an_application_job(manager):
    caller = ExecutionContext(request_context=RequestContext("assistant", session_id="session-1"))
    task_id = manager.register(metadata=task_metadata(caller, {}))
    manager.notify_completed(task_id, {"summary": "legacy result"})
    result = get_scoped_task_status({"task_id": task_id}, caller, manager)
    assert result.data["result"] == {"summary": "legacy result"}
    manager.cleanup(task_id)
    assert not get_scoped_task_status({"task_id": task_id}, caller, manager).success
    assert get_scoped_task_status({}, caller, manager).data["total_count"] == 0


def test_native_control_rejects_a_stale_revision(manager, monkeypatch):
    runtime = manager.runtime
    projection = RuntimeTaskProjection(runtime)
    manager.bind_runtime(runtime, projection=projection)
    caller = ExecutionContext(
        app_context=SimpleNamespace(), request_context=RequestContext("assistant", session_id="session-1")
    )
    actor = owner_from_context(caller.request_context, entrypoint="smart-assistant")
    ref = runtime.submit(JobSpec("assistant.native", "test", "test"), actor).ref
    runtime.start(ref, actor)
    runtime_control = projection.control

    def publish_progress_then_control(ref, actor, action, *, expected_revision):
        runtime.update_progress(ref, actor, {"current": 1})
        return runtime_control(ref, actor, action, expected_revision=expected_revision)

    monkeypatch.setattr(projection, "control", publish_progress_then_control)
    failed = _tool_stop_task({"task_id": ref.job_id}, caller)
    assert not failed.success and failed.data["failed_task_ids"] == [ref.job_id]
    assert failed.data["status"] == "running"
    assert not runtime.cancellation_token(ref, actor).is_cancelled
    runtime.complete(ref, actor)


def test_native_terminology_stop_matches_desktop_and_respects_caller_scope(manager):
    runtime = manager.runtime
    manager.bind_runtime(runtime, projection=RuntimeTaskProjection(runtime))
    request = RequestContext(
        "owner-1",
        project_id="project-1",
        variant_id="variant-1",
        session_id="chat-a",
        permissions=frozenset({runtime.MANAGE_PERMISSION}),
    )
    app_context = SimpleNamespace()
    assistant = ExecutionContext(app_context=app_context, request_context=request)
    gui_owner = owner_from_context(replace(request, permissions=frozenset()), entrypoint="gui")
    desktop_controls = TerminologySyncTaskAdapter(runtime, gui_owner, lambda _state: None)
    plan = _plan()
    planning = TerminologySyncPlanningUseCase(inputs=None)
    planning.create_plan = lambda _request: plan
    started, finished = threading.Semaphore(0), threading.Semaphore(0)
    release = threading.Event()
    received = []

    def execute(request):
        received.append(request)
        started.release()
        assert release.wait(5)
        request.cancellation.raise_if_cancelled()
        raise AssertionError("cancelled backup would proceed to remote write")

    tasks = TerminologySyncTaskEntrypoint(runtime, SimpleNamespace(execute=execute))
    service = TerminologySyncApplicationService(contexts=_Contexts(), planning=planning, tasks=tasks, runtime=runtime)
    assistant.terminology_sync_service = service
    desktop = TerminologySyncPresenter(service, request, gui_owner)
    watch = runtime.subscribe(
        lambda _event: finished.release(), event_filter=TaskEventFilter(event_types=frozenset({JobEventType.FINISHED}))
    )
    try:
        desktop.preflight(TerminologySyncMode.BACKUP)
        summary = desktop.create_plan().summary
        gui_ref = desktop.execute()
        result = _tool_execute(asdict(summary.ref), assistant)
        assert result.success, result.message
        task_id = result.data["job_id"]
        args = {"task_id": task_id}
        assert started.acquire(timeout=2) and started.acquire(timeout=2)
        assert all(item.authorized_plan.plan is plan for item in received)
        assert manager.get_status(task_id).get("error")
        status = _tool_get_task_status(args, assistant)
        assert status.success and status.data["run_id"] == result.data["run_id"]
        assert status.data["status"] == "running"
        assert not _tool_stop_task(dict(args, action="pause"), assistant).success
        assert not _tool_get_task_status({"task_id": gui_ref.job_id}, assistant).success
        assert not _tool_stop_task({"task_id": gui_ref.job_id}, assistant).success
        with pytest.raises(TaskAccessError, match="owner scope does not match"):
            desktop_controls.cancel(JobRef.from_dict(result.data))
        assert [task["task_id"] for task in _tool_get_task_status({}, assistant).data["tasks"]] == [task_id]
        for field in ("owner_id", "session_id", "project_id", "variant_id"):
            foreign = ExecutionContext(app_context=app_context, request_context=replace(request, **{field: "foreign"}))
            assert not _tool_get_task_status(args, foreign).success
            assert not _tool_stop_task(args, foreign).success
            assert _tool_stop_task({"all_tasks": True}, foreign).data["affected_task_ids"] == []
        assert not _tool_stop_task(args, ExecutionContext(app_context=app_context)).success
        assert not any(item.cancellation.is_cancelled for item in received)

        cancelled = _tool_stop_task(args, assistant)
        assert cancelled.success and cancelled.data["affected_task_ids"] == [task_id]
        repeated = _tool_stop_task({"all_tasks": True}, assistant)
        assert cancelled.data["status"] == repeated.data["status"] == "cancelling"
        assert runtime.get(gui_ref, gui_owner).state is JobState.RUNNING
        assert desktop_controls.cancel(gui_ref).state is JobState.CANCELLING
        assert all(item.cancellation.is_cancelled for item in received)
        release.set()
        assert finished.acquire(timeout=2) and finished.acquire(timeout=2)
        assert desktop.complete_job(gui_ref).job_state is JobState.CANCELLED
        assert _tool_get_task_status(args, assistant).data["status"] == "cancelled"
        assert _tool_stop_task(args, assistant).data["status"] == "cancelled"
    finally:
        release.set()
        watch.close()
