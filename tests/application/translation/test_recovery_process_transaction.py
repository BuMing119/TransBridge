"""Mutable recovery publication remains atomic between independent processes."""

import subprocess
import sys
import time

from tests.application.translation.test_task_recovery import record
from transbridge.application.translation.task_recovery import TaskRecoveryStore

_WRITER = """
from pathlib import Path
import sys, time
from transbridge.application.translation.task_recovery import TaskRecoveryStore
root, marker, writer = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
store = TaskRecoveryStore(root)
original = store._storage.pack
def pack(*args, **kwargs):
    value = original(*args, **kwargs)
    if writer == 'A':
        (marker / 'prepared').touch()
        until = time.monotonic() + 20
        while not (marker / 'release').exists():
            if time.monotonic() > until:
                raise RuntimeError('writer release timed out')
            time.sleep(.02)
    return value
store._storage.pack = pack
(marker / ('started-' + writer)).touch()
store.update_metadata('task-one', candidate_rows=[{'writer': writer}])
(marker / ('finished-' + writer)).touch()
"""


def _wait(path):
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"Process marker did not arrive: {path.name}")
        time.sleep(0.02)


def test_pending_payload_is_not_pruned_by_another_process_before_publication(tmp_path):
    root = tmp_path / "recovery"
    store = TaskRecoveryStore(root)
    store.create({**record(), "candidate_rows": []})
    processes = []
    try:
        first = subprocess.Popen(
            [sys.executable, "-c", _WRITER, str(root), str(tmp_path), "A"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(first)
        _wait(tmp_path / "prepared")
        second = subprocess.Popen(
            [sys.executable, "-c", _WRITER, str(root), str(tmp_path), "B"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(second)
        _wait(tmp_path / "started-B")
        # A owns read/pack/publish/prune as a transaction. B has started its
        # update, but cannot publish or collect A's unpublished payload.
        time.sleep(0.1)
        assert not (tmp_path / "finished-B").exists()
        (tmp_path / "release").touch()
        for process in processes:
            output, error = process.communicate(timeout=20)
            assert process.returncode == 0, output + error
        assert TaskRecoveryStore(root).load("task-one")["candidate_rows"] == [{"writer": "B"}]
        assert len(tuple(path for path in (root / "state").rglob("*.json") if path.name != "owner.json")) == 1
    finally:
        (tmp_path / "release").touch()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
