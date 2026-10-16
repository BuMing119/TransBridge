"""Startup failures close the admitted round without executing or replaying a plan."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.result_review import read_request_review
from transbridge.application.tasks import OwnerRef
from transbridge.smart_assistant.session_controller import SessionController
from transbridge.ui.tools.smart_assistant.plan_execution_binding import PlanExecutionBinding

pytest_plugins = ["tests.smart_assistant.test_request_execution"]


@pytest.mark.parametrize(
    "stage",
    [
        "runtime",
        "bridge",
        "router",
        "request_subscription",
        "engine",
        "register_callbacks",
        "subscribe",
        "thread",
        "lease",
    ],
)
def test_plan_start_failure_reaches_original_feedback_and_releases_round(setup, monkeypatch, stage):
    service, context, gate, ctx, manager = setup
    controller = SessionController()
    steps = [{"id": 1, "tool": "request_test_write", "args": {}}]
    controller.restore_confirmation({"mode": "plan", "steps": steps})
    feedback, failures = [], []
    engine = Mock()
    model_starts = []
    subscriptions = []
    controller._on_llm_round_start = lambda: model_starts.append(True)

    def fail(*args, **kwargs):
        raise OSError(f"injected {stage} startup failure")

    def finish_failure(message):
        failures.append(message)
        service.scheduler.release(gate.admission)

    monkeypatch.setattr("transbridge.smart_assistant.execution_engine.ExecutionEngine", lambda *a, **kw: engine)
    if stage == "runtime":
        monkeypatch.setattr("transbridge.smart_assistant.plan_task_runtime.PlanTaskRuntime", fail)
    elif stage in {"bridge", "router", "request_subscription"}:
        subscribe = manager.runtime.subscribe

        def watch_subscriptions(*args, **kwargs):
            if stage == "bridge" or (stage == "router" and len(subscriptions) == 1):
                fail()
            subscription = subscribe(*args, **kwargs)
            subscriptions.append(subscription)
            return subscription

        monkeypatch.setattr(manager.runtime, "subscribe", watch_subscriptions)
        if stage == "request_subscription":
            monkeypatch.setattr(service, "subscribe", fail)
    elif stage == "engine":
        monkeypatch.setattr("transbridge.smart_assistant.execution_engine.ExecutionEngine", fail)
    elif stage == "register_callbacks":
        engine.on_step_started.side_effect = fail
    elif stage == "thread":
        monkeypatch.setattr(manager, "start_thread", fail)

    def on_task_started(*_):
        if stage == "subscribe":
            # Leave runtime/bridge registration intact; fail the plan's own observer.
            monkeypatch.setattr(manager.runtime, "subscribe", fail)
        elif stage == "lease":
            service.scheduler.release(gate.admission)

    binding = PlanExecutionBinding(
        context=ctx,
        controller=lambda: controller,
        middlewares=list,
        observability=Mock(),
        conversation=Mock(),
        hide_thinking=lambda: None,
        system_message=lambda _: None,
        on_task_started=on_task_started,
        on_execution_failed=finish_failure,
    )
    view = SimpleNamespace(
        plan_feedback=lambda _: lambda *args: feedback.append(args), dispatch=lambda callback: callback()
    )
    binding.confirm(steps, view)
    try:
        assert controller.state.value == "idle"
        assert controller.pending_confirmation_id is None
        assert binding.engine is None
        assert len(feedback) == len(failures) == 1
        assert feedback[0][0] == "set_failure"
        assert "TURN_LEASE_STALE" in failures[0] if stage == "lease" else stage in failures[0]
        assert model_starts == []
        engine.execute.assert_not_called()
        with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
            gate._validate(gate.current_request(), "request_test_read")
        if stage in {"bridge", "router", "request_subscription"}:
            assert not gate.current_request().dispatches
            jobs = manager.runtime.list(OwnerRef("owner", "smart-assistant", session_id=context.session_id))
            assert jobs and all(job.is_terminal for job in jobs)
            assert all(subscription.closed for subscription in subscriptions)
            assert not hasattr(service, "request_task_events")
        elif stage != "runtime":
            (task_id,) = manager.list_all()
            assert manager.get_status(task_id)["status"] == "failed"
            assert manager.get_handle(task_id).notified
            request = gate.current_request()
            assert request.dispatches[0].status == "failed"
            assert not request.effects
            review = read_request_review(service, context, request.request_id)
            assert any(stage in row.message or "TURN_LEASE_STALE" in row.message for row in review.failures)
    finally:
        binding.close()


def test_unexpected_execution_failure_does_not_restart_the_model_or_touch_new_round():
    controller = SessionController()
    steps = [{"id": 1, "tool": "read", "args": {}}]
    controller.restore_confirmation({"mode": "plan", "steps": steps})
    controller.handle_user_confirmed(steps, "plan")
    feedback, failures, model_starts = [], [], []
    controller._on_llm_round_start = lambda: model_starts.append(True)
    binding = PlanExecutionBinding(
        context=None,
        controller=lambda: controller,
        middlewares=list,
        observability=Mock(),
        conversation=Mock(),
        hide_thinking=lambda: None,
        system_message=lambda _: None,
        on_execution_failed=failures.append,
    )
    engine = Mock()
    binding._engine = engine
    binding._feedback[engine] = lambda *args: feedback.append(args)
    generation = binding._generation
    binding._on_execution_failed(engine, generation, RuntimeError("outcome needs checking"))
    assert controller.state.value == "idle"
    assert len(feedback) == len(failures) == 1
    assert not model_starts
    newer = Mock()
    binding._engine = newer
    binding._on_execution_failed(engine, generation, RuntimeError("late failure"))
    assert binding.engine is newer
    assert len(feedback) == len(failures) == 1
    binding.close()


def test_failed_history_or_resource_cleanup_does_not_keep_the_round_active():
    controller = SessionController()
    steps = [{"id": 1, "tool": "read", "args": {}}]
    controller.restore_confirmation({"mode": "plan", "steps": steps})
    controller.handle_user_confirmed(steps, "plan")
    conversation, runtime, engine = Mock(), Mock(), Mock()
    conversation.add_plan_result.side_effect = OSError("history unavailable")
    runtime.close.side_effect = OSError("cancel delivery failed")
    failures, feedback = [], []
    binding = PlanExecutionBinding(
        context=None,
        controller=lambda: controller,
        middlewares=list,
        observability=Mock(),
        conversation=conversation,
        hide_thinking=lambda: None,
        system_message=lambda _: None,
        on_execution_failed=failures.append,
    )
    binding._engine = engine
    binding._runtimes[engine] = runtime
    binding._feedback[engine] = lambda *args: feedback.append(args)
    binding._on_execution_failed(engine, binding._generation, OSError("actual execution failure"))
    assert controller.state.value == "idle" and binding.engine is None
    assert len(failures) == len(feedback) == 1
    assert "actual execution failure" in failures[0]
    engine._executor.shutdown.assert_called_once_with(wait=False)
    binding.close()
