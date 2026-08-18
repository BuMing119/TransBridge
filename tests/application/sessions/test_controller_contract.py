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
    safe = ControllerSnapshot(ControllerState.AWAITING_CONFIRM, 2, True)
    restored = controller.restore_recovery_snapshot(safe)

    assert restored == safe
    assert controller.state is SessionController.State.AWAITING_CONFIRM
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
