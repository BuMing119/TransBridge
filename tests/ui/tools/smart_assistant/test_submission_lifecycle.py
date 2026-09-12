from __future__ import annotations

import threading
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.smart_assistant.test_v2_chat_recovery import _until
from transbridge.application.sessions import ControllerSnapshot, ControllerState
from transbridge.smart_assistant.guardrails.permission import PermissionGuard
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools.task_control import control_tasks
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.ui.tools.smart_assistant.chat_composition import _task_started
from transbridge.ui.tools.smart_assistant.submission_binding import SubmissionBinding

pytest_plugins = ["tests.ui.tools.smart_assistant.test_v2_chat_recovery"]


def test_queued_submission_cannot_start_after_replacement_switch_or_close():
    events = []
    binding = SubmissionBinding(
        interrupt=lambda: events.append("interrupt"),
        append=lambda text: events.append(("user", text)),
        start=lambda text: events.append(("start", text)),
    )
    binding.submit("first")
    binding.submit("second")
    QApplication.processEvents()
    assert ("start", "first") not in events
    assert ("start", "second") in events
    binding.submit("old session")
    binding.invalidate()
    QApplication.processEvents()
    assert ("start", "old session") not in events
    binding.submit("closing")
    binding.close()
    QApplication.processEvents()
    assert ("start", "closing") not in events


@pytest.fixture
def manager():
    TaskManager.reset()
    yield TaskManager()
    TaskManager.reset()


def _waiting(panel, manager):
    chat = panel.chat
    controller = chat._controller
    started = []
    controller._on_llm_round_start = lambda: started.append(True)
    execute = controller._on_execute_react_async
    controller._on_execute_react_async = lambda _: True
    steps = [{"tool": "start_translation", "args": {}}]
    controller.handle_user_message("translate")
    controller.handle_llm_response({"steps": steps})
    controller.handle_user_confirmed(steps)
    captured = chat._tool_handler.build_execution_context()
    task_id = manager.register(metadata=task_metadata(captured, {"type": "translation"}))
    run_id = manager.get_status(task_id)["run_id"]
    _task_started(chat, task_id, run_id)
    controller._on_execute_react_async = execute
    chat._task_binding.start()
    return task_id, run_id, started, captured


def test_real_panel_cancellation_closes_old_task_without_resuming_it(chat_environment, manager, monkeypatch):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    task_id, _, starts, captured = _waiting(panel, manager)
    spec = ToolSpec(
        "stop_task",
        "停止",
        "Stop a scoped task",
        {"task_id": {"type": "str", "required": True}},
        execute=lambda args, ctx: control_tasks(args, ctx, manager),
        permission="write",
    )
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", {"translator": {"stop_task": spec}})
    panel.chat._tool_handler._middlewares = [PermissionGuard(write_require_confirm=True)]
    panel.chat.send_user_message("取消前面的任务")
    _until(lambda: len(starts) == 2)
    before_terminal = len(panel.chat._conversation.get_messages())
    panel.chat._controller.handle_llm_response({"steps": [{"tool": "stop_task", "args": {"task_id": task_id}}]})
    _until(lambda: manager.get_status(task_id)["status"] == "cancelled")
    QApplication.processEvents()
    assert len(starts) == 3  # New cancellation request + its tool result, never the old task.
    assert panel.chat._controller.to_recovery_snapshot().active_task_id is None
    assert panel.chat._controller.state.value == "thinking"
    additions = panel.chat._conversation.get_messages()[before_terminal:]
    assert not any("[Tool result - start_translation]" in message.get("content", "") for message in additions)
    assert captured.request_context.session_id == panel._current_session_id()
    assert panel._persist_authoritative_chat()
    snapshot = chat_environment.lifecycle.active.aggregate.snapshot()
    assert snapshot.jobs[0].state.value == "cancelled"


def test_lifecycle_snapshot_reconnects_only_live_same_session_task(chat_environment, manager):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    task_id, run_id, _, captured = _waiting(panel, manager)
    session_id = panel._current_session_id()
    # Admission itself must persist identity, before any terminal notification.
    snapshot = chat_environment.lifecycle.active.aggregate.snapshot()
    assert snapshot.controller.active_task_id == task_id
    assert snapshot.controller.active_run_id == run_id
    assert snapshot.jobs[0].ref.job_id == task_id
    panel._on_create_session("second")
    assert panel._current_session_id() != session_id
    assert manager.get_status(task_id)["status"] == "running"
    assert captured.request_context.session_id == session_id
    assert panel.chat._tool_handler.build_execution_context().request_context.session_id != session_id
    panel._switch_to(session_id)
    assert panel.chat._controller.state.value == "awaiting_task"
    assert panel.chat._controller.is_awaiting_task(task_id, run_id)
    assert manager.cancel(task_id)
    _until(lambda: panel.chat._controller.state.value == "idle")
    assert panel.chat.recovery_snapshot()[1].active_task_id is None
    assert panel._persist_authoritative_chat()
    assert chat_environment.lifecycle.active.aggregate.snapshot().recovery.value == "complete"
    manager.cleanup(task_id)
    assert panel._persist_authoritative_chat()
    retained = chat_environment.lifecycle.active.aggregate.snapshot().jobs
    assert len(retained) == 1
    assert retained[0].state.value == "cancelled"


def test_forged_foreign_task_snapshot_degrades_without_subscribing_to_foreign_history(chat_environment, manager):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    foreign = manager.register(metadata={"owner_id": "other", "session_id": "other-session"})
    run_id = manager.get_status(foreign)["run_id"]
    snapshot = ControllerSnapshot(
        ControllerState.AWAITING_TASK,
        active_task_id=foreign,
        active_run_id=run_id,
    )
    panel.chat._restore_session_controller(snapshot)
    assert panel.chat._controller.state.value == "idle"
    assert panel.chat.recovery_snapshot()[1].active_task_id is None
    before = panel.chat._conversation.get_messages()
    manager.cancel(foreign)
    QApplication.processEvents()
    assert panel.chat._conversation.get_messages() == before


def test_scoped_stop_skips_redundant_confirmation_but_batch_requires_it(monkeypatch):
    spec = ToolSpec("stop_task", "Stop", "Stop", {}, permission="write")
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", {"translator": {"stop_task": spec}})
    guard = PermissionGuard(write_require_confirm=True)
    context = SimpleNamespace(owner_id="owner")
    assert guard.before_execute({"tool": "stop_task", "args": {"task_id": "one"}}, context).allowed
    result = guard.before_execute({"tool": "stop_task", "args": {"all_tasks": True}}, context)
    assert not result.allowed
    assert result.requires_confirmation == "write"


def test_real_panel_session_switch_invalidates_queued_user_round(chat_environment):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    starts = []
    panel.chat._controller._on_llm_round_start = lambda: starts.append(True)
    panel.chat.send_user_message("old session request")
    panel._on_create_session("new session")
    QApplication.processEvents()
    assert starts == []
    assert panel.chat._controller.state.value == "idle"
    assert panel.chat._conversation.get_messages() == []


def test_interrupted_round_reason_survives_repeated_gui_restore(chat_environment, manager):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    context = panel.chat._tool_handler.build_execution_context()
    task_id = manager.register(metadata=task_metadata(context, {}))
    interrupted = ControllerSnapshot(
        ControllerState.THINKING,
        recoverable=False,
        reason="interrupted_round",
        active_task_id=task_id,
        active_run_id=manager.get_status(task_id)["run_id"],
    )
    panel.chat._restore_session_controller(interrupted)
    first = panel.chat.recovery_snapshot()[1]
    assert first.state is ControllerState.IDLE
    assert first.reason == "interrupted_round"
    panel.chat.load_session({"messages": [], "controller": first.to_dict()})
    assert panel.chat.recovery_snapshot()[1] == first


def test_detached_synchronous_plan_remains_scoped_cancellable_and_persisted(chat_environment, manager, monkeypatch):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    entered, release = threading.Event(), threading.Event()
    later = []

    def blocking(_args, _context):
        entered.set()
        assert release.wait(5)
        return {"success": True, "message": "first done"}

    registry = {
        "first": ToolSpec("first", "First", "First", {}, execute=blocking),
        "later": ToolSpec("later", "Later", "Later", {}, execute=lambda *_: later.append(True)),
    }
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", {"test": registry})
    panel.chat._plan_execution._middlewares = lambda: []
    starts = []
    controller = panel.chat._controller
    controller._on_llm_round_start = lambda: starts.append(True)
    steps = [
        {"id": 1, "tool": "first", "args": {}},
        {"id": 2, "tool": "later", "args": {}, "depends_on": [1]},
    ]
    controller.handle_user_message("run plan")
    controller.handle_llm_response({"mode": "plan", "steps": steps})
    panel.chat._plan_execution.confirm(steps, panel.chat._confirmation_view)
    try:
        _until(entered.is_set)
        jobs = chat_environment.lifecycle.active.aggregate.snapshot().jobs
        assert len(jobs) == 1
        task_id = jobs[0].ref.job_id
        assert manager.get_status(task_id)["metadata"]["type"] == "assistant-plan"
        panel.chat.send_user_message("取消前面的计划")
        _until(lambda: len(starts) == 2)
        assert manager.get_status(task_id)["status"] == "running"
        result = control_tasks({}, panel.chat._tool_handler.build_execution_context(), manager)
        assert result.success
        assert result.data["task_id"] == task_id
        assert manager.get_status(task_id)["status"] == "cancelling"
        release.set()
        _until(lambda: manager.get_status(task_id)["status"] == "cancelled")
        _until(lambda: not panel.chat._plan_execution._retired_engines)
        assert later == []
        assert len(starts) == 2
        assert panel._persist_authoritative_chat()
        assert chat_environment.lifecycle.active.aggregate.snapshot().jobs[0].state.value == "cancelled"
    finally:
        release.set()
