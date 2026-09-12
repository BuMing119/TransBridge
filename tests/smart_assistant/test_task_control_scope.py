from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from transbridge.smart_assistant.tools.task_control import control_tasks, get_scoped_task_status
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata


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
