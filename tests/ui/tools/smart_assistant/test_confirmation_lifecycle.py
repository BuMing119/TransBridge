from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
import pytest

from transbridge.smart_assistant.execution_engine import StepResult
from transbridge.smart_assistant.plan_task_runtime import PlanTaskRuntime
from transbridge.smart_assistant.session_controller import SessionController
from transbridge.ui.tools.smart_assistant.confirmation_view import ConfirmationView, PlanExecutionBinding

_APP = QApplication.instance() or QApplication([])


def _view(engine=None):
    parent = QWidget()
    intents = []
    cards = []
    view = ConfirmationView(
        parent=parent,
        add_widget=cards.append,
        plan_confirmed=lambda steps: intents.append(("plan", steps)),
        plan_cancelled=lambda: intents.append(("cancel_plan", None)),
        tool_executed=lambda step: intents.append(("tool", step)),
        tool_ignored=lambda step: intents.append(("ignore_tool", step)),
        batch_executed=lambda steps: intents.append(("batch", steps)),
        batch_ignored=lambda steps: intents.append(("ignore_batch", steps)),
        engine=engine or (lambda: None),
    )
    return view, parent, intents, cards


@pytest.mark.parametrize("kind", ["tool", "batch", "plan"])
def test_expired_confirmation_cannot_execute_when_new_confirmation_is_pending(kind):
    view, parent, intents, cards = _view()
    old_step = {"id": 1, "tool": "old_task", "args": {}}
    new_step = {"id": 2, "tool": "new_task", "args": {}}
    create = getattr(view, f"add_{'batch_tool' if kind == 'batch' else kind}_card")
    old = create(old_step if kind == "tool" else [old_step])

    view.invalidate_pending()
    current = create(new_step if kind == "tool" else [new_step])
    assert not old._exec_btn.isEnabled()
    assert "失效" in old._exec_btn.text()
    assert "失效" in old.accessibleDescription()
    # Late Qt deliveries must be rejected even if they bypass disabled buttons.
    if kind == "tool":
        old.executed.emit(old_step)
        old.ignored.emit(old_step)
    elif kind == "batch":
        old.all_executed.emit([old_step])
        old.all_ignored.emit([old_step])
    else:
        old.confirmed.emit([old_step])
        old.cancelled.emit()
    assert intents == []

    current._exec_btn.click()
    assert intents == [(kind, new_step if kind == "tool" else [new_step])]
    view.close()
    parent.close()


def test_confirmation_decision_is_one_shot_and_close_expires_pending_cards():
    view, parent, intents, cards = _view()
    step = {"tool": "translate"}
    consumed = view.add_tool_card(step)
    consumed._exec_btn.click()
    consumed.executed.emit(step)
    consumed.ignored.emit(step)
    consumed._on_execute()
    assert intents == [("tool", step)]

    pending = view.add_tool_card(step)
    view.close()
    view.close()
    assert consumed._exec_btn.text() == "执行中..."
    assert pending._exec_btn.text() == "已失效"
    pending.executed.emit(step)
    assert intents == [("tool", step)]
    parent.close()


def test_invalidating_queued_engine_decision_releases_worker_without_default_approval(monkeypatch):
    engine = Mock()
    view, parent, _, _ = _view(lambda: engine)
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", question)
    queued = threading.Event()
    view._bridge.requested.connect(lambda callback: queued.set(), Qt.ConnectionType.DirectConnection)
    worker = threading.Thread(target=view.request_engine_decision, args=("node", "confirm?", ["yes", "no"]))
    worker.start()
    try:
        assert queued.wait(2), "decision should be queued before cancellation"
        view.invalidate_pending()
        worker.join(2)
        assert not worker.is_alive()
        _APP.processEvents()
        question.assert_not_called()
        engine.provide_decision.assert_not_called()
    finally:
        view.close()
        worker.join(2)
        parent.close()


@pytest.mark.parametrize("change", ["replace_engine", "invalidate", "replace_generation"])
def test_dialog_reply_never_reaches_replaced_engine_or_expired_request(monkeypatch, change):
    original = Mock()
    replacement = Mock()
    current = [original]
    generation = [1]
    view, parent, _, _ = _view(lambda: current[0])

    def answer(*args):
        if change == "replace_engine":
            current[0] = replacement
        elif change == "invalidate":
            view.invalidate_pending()
        else:
            generation[0] = 2
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    view.request_engine_decision(
        "node", "confirm?", ["yes", "no"], engine=original, is_current=lambda: generation[0] == 1
    )
    original.provide_decision.assert_not_called()
    replacement.provide_decision.assert_not_called()
    view.close()
    parent.close()


def test_engine_decision_accepts_current_owner_and_rejects_obsolete_callback(monkeypatch):
    original = Mock()
    current = Mock()
    view, parent, _, _ = _view(lambda: current)
    question = Mock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", question)
    view.request_engine_decision("node-old", "obsolete?", ["yes", "no"], engine=original)
    question.assert_not_called()
    view.request_engine_decision("node-new", "current?", ["yes", "no"], engine=current)
    current.provide_decision.assert_called_once_with("node-new", "no")
    original.provide_decision.assert_not_called()
    view.close()
    parent.close()


@pytest.mark.parametrize("action", ["invalidate_pending", "close"])
def test_permission_reply_is_rejected_after_request_lifecycle_changes(monkeypatch, action):
    view, parent, _, _ = _view()

    def answer(*args):
        getattr(view, action)()
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    assert view.ask_permission("permission", "allow?") is False
    view.close()
    parent.close()


def _plan_binding():
    controller = Mock()
    conversation = Mock()
    observability = Mock()
    messages = []
    binding = PlanExecutionBinding(
        context=object(),
        controller=lambda: controller,
        middlewares=list,
        observability=observability,
        conversation=conversation,
        hide_thinking=lambda: None,
        system_message=messages.append,
    )
    return binding, controller, conversation, observability, messages


@pytest.mark.parametrize("failed", [False, True])
def test_interrupted_plan_releases_on_completion_without_advancing_new_conversation(failed):
    binding, controller, conversation, observability, messages = _plan_binding()
    old_engine, new_engine = Mock(), Mock()
    binding._engine = old_engine
    generation = binding._generation
    binding.interrupt_round()
    assert binding.engine is None
    old_engine.cancel.assert_not_called()
    old_engine._executor.shutdown.assert_not_called()
    observability.end_conversation.assert_called_once()

    binding._engine = new_engine
    if failed:
        binding._on_execution_failed(old_engine, generation, RuntimeError("detached failure"))
    else:
        binding._on_all_finished(old_engine, generation, [])
    assert binding.engine is new_engine
    assert not binding._retired_engines
    old_engine._executor.shutdown.assert_called_once_with(wait=False)
    new_engine.cancel.assert_not_called()
    controller.handle_execution_complete.assert_not_called()
    conversation.add_plan_result.assert_not_called()
    assert messages == []
    binding.close()


def test_close_cancels_and_releases_active_and_detached_plans():
    binding, *_ = _plan_binding()
    old_engine, new_engine = Mock(), Mock()
    binding._engine = old_engine
    binding.interrupt_round()
    binding._engine = new_engine
    binding.close()
    binding.close()
    assert binding.engine is None
    assert not binding._retired_engines
    for engine in (old_engine, new_engine):
        engine.cancel.assert_called_once()
        engine._executor.shutdown.assert_called_once_with(wait=False)


def test_detached_plan_rejects_queued_confirmation_without_cancelling_existing_child(monkeypatch):
    binding, *_ = _plan_binding()
    engine = Mock()
    binding._engine = engine
    generation = binding._generation
    view, parent, _, _ = _view(lambda: binding.engine)
    queued = threading.Event()
    view._bridge.requested.connect(lambda callback: queued.set(), Qt.ConnectionType.DirectConnection)
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", question)
    worker = threading.Thread(
        target=binding._request_engine_decision,
        args=(engine, generation, view, "old-node", "confirm?", ["继续", "跳过"]),
    )
    worker.start()
    try:
        assert queued.wait(2)
        # Retire before releasing the queued dialog waiter, so it can reject the
        # old plan's decision without granting authority to a new request.
        binding.interrupt_round()
        view.invalidate_pending()
        worker.join(2)
        assert not worker.is_alive()
        _APP.processEvents()
        question.assert_not_called()
        engine.cancel.assert_not_called()
        engine.provide_decision.assert_called_once_with("old-node", "终止")
    finally:
        view.close()
        binding.close()
        worker.join(2)
        parent.close()


def test_active_plan_cancelled_child_closes_real_controller_without_restarting_model():
    model_rounds = []
    controller = SessionController(on_llm_round_start=lambda: model_rounds.append(True))
    steps = [{"id": 1, "tool": "start_translation", "args": {}}]
    controller.handle_user_message("translate")
    controller.handle_llm_response({"mode": "plan", "steps": steps})
    controller.handle_user_confirmed(steps, "plan")
    assert controller.state is controller.State.EXECUTING
    binding, _, conversation, _, messages = _plan_binding()
    binding._controller = lambda: controller
    engine = Mock()
    binding._engine = engine
    results = [StepResult(1, "start_translation", False, "用户取消", data={"status": "cancelled"})]

    binding._on_all_finished(engine, binding._generation, results)

    assert controller.state is controller.State.IDLE
    assert model_rounds == [True]
    assert "计划已取消" in messages[0]
    assert "[CANCELLED]" in messages[0]
    assert "[FAIL]" not in messages[0]
    assert conversation.add_plan_result.call_args.kwargs["results"][0]["status"] == "cancelled"
    assert binding.engine is None
    engine._executor.shutdown.assert_called_once_with(wait=False)
    binding.close()


@pytest.mark.parametrize("background_child", [False, True])
def test_runtime_parent_can_cancel_detached_plan_and_its_child(tmp_path, monkeypatch, background_child):
    from transbridge.application.contracts import RequestContext
    from transbridge.application.tasks import TaskCancelled
    from transbridge.smart_assistant.execution_engine import ExecutionEngine
    from transbridge.smart_assistant.tool_registry import ToolSpec
    from transbridge.smart_assistant.tools.base import ExecutionContext, ToolResult
    from transbridge.smart_assistant.tools.task_control import control_tasks
    from transbridge.smart_assistant.tools.task_manager import TaskManager
    from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata

    monkeypatch.setattr(TaskManager, "_instance", None)
    monkeypatch.setattr(TaskManager, "_dispatcher", lambda callback: callback())
    manager = TaskManager()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    later_steps, completions, failures, child_ids = [], [], [], []
    ctx = ExecutionContext(
        app_context=SimpleNamespace(project_path=tmp_path),
        request_context=RequestContext("owner", session_id="session"),
    )
    parent = PlanTaskRuntime(ctx, manager=manager)

    def first(args, execution_ctx):
        if background_child:
            child_id = manager.register(metadata=task_metadata(execution_ctx, {"type": "test-child"}))
            child_ids.append(child_id)

            def child():
                manager.get_handle(child_id).stop_event.wait(3)
                raise TaskCancelled("child cancelled")

            manager.start_thread(child_id, child)
            started.set()
            return ToolResult.ok("started", data={"task_id": child_id})
        started.set()
        assert release.wait(3)
        return ToolResult.ok("first done")

    specs = {
        "first": ToolSpec("first", "first", "first", {}, execute=first, is_long_running=background_child),
        "later": ToolSpec(
            "later",
            "later",
            "later",
            {},
            execute=lambda args, context: later_steps.append(True) or ToolResult.ok("done"),
        ),
    }
    engine = ExecutionEngine(SimpleNamespace(get=lambda name, **kwargs: specs[name]), parent.execution_context)
    engine._executor._guards = []
    binding, *_ = _plan_binding()
    binding._engine = engine
    binding._runtimes[engine] = parent
    steps = [{"id": 1, "tool": "first"}, {"id": 2, "tool": "later"}]
    parent.start(
        engine,
        steps,
        finished=lambda results: (completions.extend(results), finished.set()),
        failed=lambda error: (failures.append(error), finished.set()),
    )
    try:
        assert started.wait(2)
        binding.interrupt_round()
        assert manager.get_status(parent.task_id)["status"] == "running"
        if background_child:
            child_status = manager.get_status(child_ids[0])
            assert child_status["metadata"]["parent_task_id"] == parent.task_id
        stopped = control_tasks({}, ctx, manager)
        assert stopped.success
        assert stopped.data["affected_task_ids"] == [parent.task_id]
        assert stopped.data["status"] == "cancelling"
        release.set()
        assert finished.wait(2)
        manager.get_handle(parent.task_id)._thread.join(2)
        assert manager.get_status(parent.task_id)["status"] == "cancelled"
        assert not manager.get_handle(parent.task_id)._thread.is_alive()
        assert failures == []
        assert later_steps == []
        assert any(isinstance(result.data, dict) and result.data.get("status") == "cancelled" for result in completions)
        assert parent._subscription.closed
        if background_child:
            assert manager.get_status(child_ids[0])["status"] == "cancelled"
    finally:
        release.set()
        binding.close()
        TaskManager.reset()


@pytest.mark.parametrize("outcome", ["completed", "failed", "exception"])
def test_plan_parent_records_actual_terminal_and_closes_subscription(monkeypatch, outcome):
    from transbridge.application.contracts import RequestContext
    from transbridge.smart_assistant.tools.base import ExecutionContext
    from transbridge.smart_assistant.tools.task_manager import TaskManager

    monkeypatch.setattr(TaskManager, "_instance", None)
    monkeypatch.setattr(TaskManager, "_dispatcher", lambda callback: callback())
    manager = TaskManager()
    parent = PlanTaskRuntime(ExecutionContext(request_context=RequestContext("owner")), manager=manager)
    engine = Mock()
    if outcome == "exception":
        engine.execute.side_effect = ValueError("invalid graph")
    else:
        engine.execute.return_value = [StepResult(1, "read", outcome == "completed", outcome)]
    finished, results, errors = threading.Event(), [], []
    parent.start(
        engine,
        [],
        finished=lambda items: (results.extend(items), finished.set()),
        failed=lambda error: (errors.append(error), finished.set()),
    )
    try:
        assert finished.wait(2)
        manager.get_handle(parent.task_id)._thread.join(2)
        expected = "completed" if outcome == "completed" else "failed"
        assert manager.get_status(parent.task_id)["status"] == expected
        assert parent._subscription.closed
        assert len(errors) == (1 if outcome == "exception" else 0)
        assert len(results) == (0 if outcome == "exception" else 1)
    finally:
        parent.close()
        TaskManager.reset()


@pytest.mark.parametrize("terminal_before_start", [False, True])
def test_plan_cancelled_before_start_never_executes_and_reconciles_terminal(monkeypatch, terminal_before_start):
    from transbridge.application.contracts import RequestContext
    from transbridge.smart_assistant.tools.base import ExecutionContext
    from transbridge.smart_assistant.tools.task_manager import TaskManager

    monkeypatch.setattr(TaskManager, "_instance", None)
    monkeypatch.setattr(TaskManager, "_dispatcher", lambda callback: callback())
    manager = TaskManager()
    parent = PlanTaskRuntime(ExecutionContext(request_context=RequestContext("owner")), manager=manager)
    parent.close() if terminal_before_start else parent.cancel()
    engine = Mock()
    completed, results, errors = threading.Event(), [], []
    try:
        parent.start(
            engine,
            [],
            finished=lambda items: (results.extend(items), completed.set()),
            failed=lambda error: (errors.append(error), completed.set()),
        )
        assert completed.wait(2)
        engine.execute.assert_not_called()
        assert manager.get_status(parent.task_id)["status"] == "cancelled"
        assert results[0].data["status"] == "cancelled"
        assert not errors
        assert parent._subscription.closed
    finally:
        parent.close()
        TaskManager.reset()
