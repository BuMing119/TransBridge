from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from transbridge.application.sessions import ControllerSnapshot, ControllerState
from transbridge.smart_assistant.session_controller import SessionController, SessionTransitionError


def test_controller_invalid_transition_is_typed_domain_error() -> None:
    controller = SessionController()

    with pytest.raises(SessionTransitionError) as captured:
        controller.handle_task_started()

    assert captured.value.code == "SESSION_STATE_TRANSITION_INVALID"
    assert captured.value.details == {
        "action": "handle_task_started",
        "current": "idle",
        "expected": ["executing"],
    }


def test_controller_recovery_restores_only_safe_states() -> None:
    controller = SessionController()
    safe = ControllerSnapshot(ControllerState.IDLE, 2, True)
    restored = controller.restore_recovery_snapshot(safe)

    assert restored == safe
    assert controller.state is SessionController.State.IDLE
    assert controller.react_depth == 2
    assert controller.auto_mode

    unsafe = ControllerSnapshot(
        ControllerState.EXECUTING,
        3,
        True,
        False,
        "job unavailable",
    )
    degraded = controller.restore_recovery_snapshot(unsafe)
    assert degraded.state is ControllerState.IDLE
    assert not degraded.recoverable
    assert controller.state is SessionController.State.IDLE


def test_controller_never_restores_confirmation_without_executable_payload() -> None:
    controller = SessionController()
    restored = controller.restore_recovery_snapshot(ControllerSnapshot(ControllerState.AWAITING_CONFIRM))
    assert restored.state is ControllerState.IDLE
    assert restored.reason == "pending_confirmation_payload_unavailable"
    assert controller.pending_confirmation_id is None


def test_controller_snapshot_roundtrip_preserves_task_identity() -> None:
    controller = SessionController()
    controller._state = SessionController.State.EXECUTING
    controller.handle_task_started("task", "run")
    snapshot = controller.to_recovery_snapshot()
    assert ControllerSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert (snapshot.active_task_id, snapshot.active_run_id) == ("task", "run")
    assert not snapshot.recoverable


def test_controller_restores_reconciled_waiting_task_without_restarting_execution() -> None:
    controller = SessionController()
    snapshot = ControllerSnapshot(ControllerState.AWAITING_TASK, active_task_id="task", active_run_id="run")
    assert controller.restore_recovery_snapshot(snapshot, task_verified=True) == snapshot
    assert controller.is_awaiting_task("task", "run")
    assert controller.handle_task_completed("task", {"cancelled": True}, "run")
    assert controller.state is SessionController.State.IDLE


def test_persisted_recoverable_flag_does_not_replace_runtime_verification() -> None:
    controller = SessionController()
    snapshot = ControllerSnapshot(ControllerState.AWAITING_TASK, active_task_id="task", active_run_id="run")
    restored = controller.restore_recovery_snapshot(snapshot)
    assert restored.state is ControllerState.IDLE
    assert restored.reason == "task_runtime_verification_required"
    assert not controller.accepts_task_completion("task", "run")


def test_interrupted_round_restoration_retains_verified_background_task_without_resuming() -> None:
    controller = SessionController()
    snapshot = ControllerSnapshot(
        ControllerState.THINKING,
        recoverable=False,
        reason="in_flight_round_cannot_be_resumed",
        active_task_id="task",
        active_run_id="run",
    )
    restored = controller.restore_recovery_snapshot(snapshot, task_verified=True)
    assert restored.state is ControllerState.IDLE
    assert not restored.recoverable
    assert restored.active_task_id == "task"
    assert controller.accepts_task_completion("task", "run")
    assert not controller.is_awaiting_task("task", "run")
    controller.handle_task_completed("task", {}, "run")
    assert controller.state is SessionController.State.IDLE


def test_degraded_restore_reason_survives_immediate_resave_until_fresh_round() -> None:
    controller = SessionController()
    restored = controller.restore_recovery_snapshot(ControllerSnapshot(ControllerState.AWAITING_CONFIRM))
    resaved = controller.to_recovery_snapshot()
    assert resaved == restored
    assert resaved.reason == "pending_confirmation_payload_unavailable"
    controller.handle_user_message("new request")
    controller.handle_llm_response({"steps": []})
    assert controller.to_recovery_snapshot().recoverable
    assert controller.to_recovery_snapshot().reason is None


def test_matched_completion_clears_restored_degradation_but_stale_completion_does_not() -> None:
    controller = SessionController()
    snapshot = ControllerSnapshot(
        ControllerState.THINKING,
        recoverable=False,
        reason="in_flight_round_cannot_be_resumed",
        active_task_id="task",
        active_run_id="run",
    )
    controller.restore_recovery_snapshot(snapshot, task_verified=True)
    controller.handle_task_completed("task", {}, "old-run")
    assert controller.to_recovery_snapshot().reason == snapshot.reason
    controller.handle_task_completed("task", {}, "run")
    assert controller.to_recovery_snapshot().recoverable
    assert controller.to_recovery_snapshot().reason is None


def test_session_detach_clears_previous_recovery_reason_but_round_interrupt_preserves_it() -> None:
    controller = SessionController()
    controller.restore_recovery_snapshot(ControllerSnapshot(ControllerState.AWAITING_CONFIRM))
    controller.handle_round_interrupted()
    assert controller.to_recovery_snapshot().reason == "pending_confirmation_payload_unavailable"
    controller.handle_abort()
    snapshot = controller.to_recovery_snapshot()
    assert snapshot.state is ControllerState.IDLE
    assert snapshot.recoverable
    assert snapshot.reason is None


def test_python_optimized_mode_cannot_disable_transition_validation() -> None:
    code = (
        "from transbridge.smart_assistant.session_controller import "
        "SessionController, SessionTransitionError\n"
        "try:\n"
        "    SessionController().handle_task_started()\n"
        "except SessionTransitionError as exc:\n"
        "    print(exc.code)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-O", "-c", code],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[3] / "src"),
        },
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "SESSION_STATE_TRANSITION_INVALID"
