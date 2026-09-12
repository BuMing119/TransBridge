from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.ui.tools.smart_assistant.task_binding import TaskBinding

_APP = QApplication.instance() or QApplication([])


def status(*, session="session-1", run="run-1", state="cancelled"):
    return {
        "task_id": "task-1",
        "run_id": run,
        "status": state,
        "owner": {"owner_id": "assistant", "session_id": session},
    }


@pytest.fixture
def harness():
    parent = QWidget()
    messages, observations, completions, saved = [], [], [], []
    model = SimpleNamespace(awaited=False, accepted=True, status=status(), scope=status()["owner"])
    controller = SimpleNamespace(
        accepts_task_completion=lambda *_: model.accepted,
        is_awaiting_task=lambda *_: model.awaited,
        handle_task_completed=lambda *args: completions.append(args),
    )
    binding = TaskBinding(
        parent=parent,
        conversation=SimpleNamespace(add_observation=lambda *args: observations.append(args)),
        system_message=messages.append,
        controller=lambda: controller,
        sanitize_error=lambda value: value,
        scope=lambda: model.scope,
        on_lifecycle_changed=lambda: saved.append(tuple(completions)),
    )
    binding._manager = SimpleNamespace(get_status=lambda _: model.status, remove_listener=lambda _: None)
    yield SimpleNamespace(
        binding=binding,
        model=model,
        messages=messages,
        observations=observations,
        completions=completions,
        saved=saved,
    )
    binding.close()


def test_detached_cancel_notifies_once_without_changing_new_round_context(harness):
    h = harness
    h.binding._on_terminal(status(), False, "用户取消", None)
    h.binding._on_terminal(status(), False, "重复事件", None)
    assert len(h.messages) == len(h.completions) == len(h.saved) == 1
    assert h.messages[0].startswith("[CANCELLED]")
    assert h.completions[0][1]["status"] == "cancelled"
    assert h.observations == []
    assert h.saved[0] == tuple(h.completions)


def test_actively_awaited_completion_adds_observation_before_resume(harness):
    h = harness
    h.model.awaited = True
    h.binding._on_terminal(status(), False, "用户取消", None)
    h.binding._on_terminal(status(), False, "重复事件", None)
    assert len(h.observations) == len(h.completions) == 1


@pytest.mark.parametrize("event", [status(session="other"), status(run="old-run"), status(state="running")])
def test_foreign_stale_or_nonterminal_event_has_no_side_effects(harness, event):
    h = harness
    h.binding._on_terminal(event, False, "invalid", None)
    assert h.messages == h.observations == h.completions == h.saved == []


def test_session_switch_drops_queued_old_event(harness):
    h = harness
    queued = status()
    h.model.scope = {"owner_id": "assistant", "session_id": "new-session"}
    h.binding._on_terminal(queued, False, "old", None)
    assert h.messages == h.observations == h.completions == []


def test_untracked_same_session_terminal_only_notifies(harness):
    h = harness
    h.model.accepted = False
    h.binding._on_terminal(status(), False, "done", None)
    assert len(h.messages) == 1
    assert h.observations == h.completions == []


def test_cancelling_snapshot_persisted_once_even_without_monitor(harness):
    h = harness
    h.model.status = status(state="cancelling")
    h.binding._on_updated("task-1")
    h.binding._on_updated("task-1")
    assert len(h.saved) == 1
    assert h.messages == h.observations == h.completions == []


def test_captured_terminal_survives_facade_cleanup(harness):
    h = harness
    h.model.status = {"error": "任务不存在"}
    h.binding._on_terminal(status(), False, "已停止", None)
    assert len(h.messages) == len(h.completions) == 1


def test_awaited_cancel_closes_real_controller_without_new_model_round(harness):
    from transbridge.smart_assistant.session_controller import SessionController

    h = harness
    rounds = []
    controller = SessionController(on_llm_round_start=lambda: rounds.append("round"))
    controller._state = controller.State.EXECUTING
    controller.handle_task_started("task-1", "run-1")
    h.binding._controller = lambda: controller
    h.binding._on_terminal(status(), False, "用户取消", None)
    assert controller.state is controller.State.IDLE
    assert not controller.accepts_task_completion("task-1", "run-1")
    assert rounds == []
    assert len(h.observations) == 1


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_fast_terminal_before_task_registration_reconciles_without_duplicate_notice(harness, terminal):
    from transbridge.smart_assistant.session_controller import SessionController

    h = harness
    rounds = []
    controller = SessionController(on_llm_round_start=lambda: rounds.append("round"))
    h.binding._controller = lambda: controller
    h.model.status = status(state=terminal)
    h.binding._on_terminal(h.model.status, terminal == "completed", "finished early", {"success_count": 1})
    assert len(h.messages) == 1
    assert h.observations == []
    controller._state = controller.State.EXECUTING
    controller.handle_task_started("task-1", "run-1")
    h.binding.reconcile_task("task-1")
    h.binding.reconcile_task("task-1")
    assert len(h.messages) == len(h.observations) == 1
    assert not controller.accepts_task_completion("task-1", "run-1")
    assert controller.state is (controller.State.IDLE if terminal == "cancelled" else controller.State.THINKING)
    assert len(rounds) == (0 if terminal == "cancelled" else 1)


def test_reconcile_task_ignores_running_task(harness):
    h = harness
    h.model.status = status(state="running")
    h.binding.reconcile_task("task-1")
    assert h.messages == h.observations == h.completions == []
