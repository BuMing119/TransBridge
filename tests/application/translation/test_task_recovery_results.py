from copy import deepcopy
import json
import subprocess
import sys

import pytest

from transbridge.application.translation.task_recovery_lease import TaskRecoveryLease
from transbridge.application.translation.task_recovery_results import TaskRecoveryResults


def row():
    return {
        "entry_key": {"namespace": "plugin:source", "local_key": "one"},
        "source": "source",
        "action": "translation",
        "before": {"original": "Name", "translation": "", "stage": 0, "context": "NPC_:FULL", "revision": 0},
        "status": "succeeded",
        "text": "名称",
        "stage": 2,
        "reason": "待复核",
        "decision": "pending",
        "applied": False,
        "attempt": 1,
        "report_details": {"review_status": "needs_review", "review_reason": "参考存在歧义"},
    }


def test_results_survive_reopen_and_preserve_decisions(tmp_path):
    store = TaskRecoveryResults(tmp_path)
    pending = row()
    accepted = {**pending, "decision": "accepted"}
    store.merge("task", [accepted])
    store.merge("task", [pending])  # An older asynchronous status delivery.
    assert TaskRecoveryResults(tmp_path).load("task")["rows"] == [accepted]
    store.merge("task", [])  # Startup cannot erase previous completed results.
    assert store.load("task")["rows"] == [accepted]


def test_corruption_is_reported_and_original_retained(tmp_path):
    store = TaskRecoveryResults(tmp_path)
    store.merge("task", [row()])
    path = store._path("task")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["payload"]["rows"][0]["text"] = "tampered"
    path.write_text(json.dumps(value), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="损坏"):
        store.load("task")
    assert path.read_bytes() == before


def test_failed_atomic_write_preserves_previous_results(tmp_path, monkeypatch):
    from transbridge.application.translation import task_recovery_results

    store = TaskRecoveryResults(tmp_path)
    original = row()
    store.merge("task", [original])
    updated = deepcopy(original)
    updated.update(decision="rejected")

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(task_recovery_results, "atomic_json", fail)
    with pytest.raises(OSError, match="disk full"):
        store.merge("task", [updated])
    assert store.load("task")["rows"] == [original]


def test_process_exit_releases_lease_and_duplicate_execution_is_blocked(tmp_path):
    script = (
        "from pathlib import Path; import os,sys; "
        "from transbridge.application.translation.task_recovery_lease import TaskRecoveryLease; "
        "lease=TaskRecoveryLease(Path(sys.argv[1]), 'task'); "
        "print('locked', flush=True); sys.stdin.readline(); os._exit(17)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match="已在其他"):
            TaskRecoveryLease(tmp_path, "task")
        child.communicate("exit\n", timeout=10)
        assert child.returncode == 17
        lease = TaskRecoveryLease(tmp_path, "task")
        lease.close()
        lease.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
