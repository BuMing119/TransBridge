from __future__ import annotations

import pytest

from transbridge.smart_assistant.tools.task_manager import TaskManager


@pytest.fixture
def manager():
    TaskManager.reset()
    value = TaskManager()
    yield value
    TaskManager.reset()


def test_legacy_register_is_projected_from_task_runtime(manager):
    task_id = manager.register(metadata={"job_type": "translation", "owner_id": "session-1"})
    handle = manager.get_handle(task_id)
    handle.status = "failed"

    status = manager.get_status(task_id)
    assert status["status"] == "running"
    assert status["metadata"]["job_type"] == "translation"


def test_arbitrary_set_status_is_rejected_and_terminal_is_immutable(manager):
    task_id = manager.register()
    assert manager.set_status(task_id, "invented") is False
    assert manager.set_status(task_id, "paused") is False
    assert manager.get_status(task_id)["status"] == "running"

    assert manager.set_status(task_id, "completed") is True
    assert manager.set_status(task_id, "failed") is False
    assert manager.get_status(task_id)["status"] == "completed"


def test_legacy_cancel_wakes_backend_and_commits_one_terminal(manager):
    task_id = manager.register()
    manager.pause(task_id)
    handle = manager.get_handle(task_id)
    assert manager.cancel(task_id) is True
    assert handle.stop_event.is_set()
    assert handle.pause_event.is_set()
    assert manager.get_status(task_id)["status"] == "cancelled"
    assert manager.set_status(task_id, "completed") is False


def test_remove_listener_removes_deprecated_callback_wrapper(manager):
    received = []

    def completed(task_id, data):
        received.append((task_id, data))

    manager.on_completed(completed)
    manager.remove_listener(completed)
    task_id = manager.register()
    manager.notify_completed(task_id, {"value": 1})

    assert received == []
    assert manager.get_status(task_id)["status"] == "completed"


def test_late_success_notification_after_cancel_is_dropped(manager):
    received = []
    manager.on_finished(lambda *args: received.append(args))
    task_id = manager.register()
    manager.cancel(task_id)

    manager.notify_completed(task_id, {"late": True})

    assert received == []
    assert manager.get_status(task_id)["status"] == "cancelled"


def test_cleanup_uses_runtime_terminal_state_not_mutable_handle_projection(manager):
    task_id = manager.register()
    manager.get_handle(task_id).status = "completed"
    assert manager.cleanup_all() == 0
    assert manager.get_handle(task_id) is not None
