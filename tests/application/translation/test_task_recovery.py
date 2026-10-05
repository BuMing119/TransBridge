from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

import pytest

from transbridge.application.translation import task_recovery
from transbridge.application.translation.task_recovery import TaskRecoveryStore


def record(task_id="task-one"):
    return {
        "task_id": task_id,
        "created_at": "2026-10-04T00:00:00+00:00",
        "project_id": "project",
        "variant_id": "variant",
        "mode": "polish",
        "config_digest": "a" * 64,
        "state": "running",
        "sources": [
            {
                "key": "source",
                "label": "Source",
                "translate_keys": [],
                "polish_keys": [{"namespace": "plugin:test", "local_key": "one"}],
            }
        ],
    }


def test_create_load_update_and_list_preserve_old_tasks(tmp_path):
    store = TaskRecoveryStore(tmp_path)
    original = record()
    saved = store.create(original)
    original["sources"][0]["label"] = "mutated caller"
    assert store.load("task-one") == saved
    later = record("task-two")
    later["created_at"] = "2026-10-04T01:00:00+00:00"
    store.create(later)
    updated = store.update("task-one", state="deferred", additional={"saved_count": 12})
    assert updated["additional"] == {"saved_count": 12}
    assert updated["state"] == "deferred" and "updated_at" in updated
    assert store.list_records() == (later, updated)
    assert TaskRecoveryStore(tmp_path).load("task-one") == updated


def test_duplicate_create_never_overwrites_existing_record(tmp_path):
    store = TaskRecoveryStore(tmp_path)
    original = store.create(record())
    with pytest.raises(FileExistsError):
        TaskRecoveryStore(tmp_path).create({**record(), "state": "new"})
    assert store.load("task-one") == original


def test_concurrent_create_has_one_winner_without_overwriting(tmp_path, monkeypatch):
    barrier = Barrier(2)
    atomic_write = task_recovery.atomic_json

    def simultaneous_write(path, value):
        atomic_write(path, value)
        barrier.wait(timeout=5)

    monkeypatch.setattr(task_recovery, "atomic_json", simultaneous_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(TaskRecoveryStore(tmp_path).create, {**record(), "state": state})
            for state in ("first", "second")
        ]
        successes = [future.result() for future in futures if future.exception() is None]
        errors = [future.exception() for future in futures if future.exception() is not None]
    assert len(successes) == len(errors) == 1
    assert isinstance(errors[0], FileExistsError)
    assert TaskRecoveryStore(tmp_path).load("task-one") == successes[0]
    assert len(tuple(tmp_path.iterdir())) == 1


def test_task_id_is_hashed_and_cannot_escape_root(tmp_path):
    store = TaskRecoveryStore(tmp_path)
    value = record("../../outside")
    assert store.create(value) == value
    files = tuple(tmp_path.iterdir())
    assert len(files) == 1 and len(files[0].stem) == 64
    assert store.load(value["task_id"]) == value


@pytest.mark.parametrize("field,value", [("task_id", ""), ("sources", {}), ("created_at", "yesterday"), ("mode", 1)])
def test_rejects_invalid_key_shapes_without_writing(tmp_path, field, value):
    with pytest.raises(ValueError):
        TaskRecoveryStore(tmp_path).create({**record(), field: value})
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    "extra",
    [
        {"api_key": "secret"},
        {"nested": [{"Authorization": "secret"}]},
        {"nested": ({"refresh_token": "secret"},)},
        {"value": float("nan")},
    ],
)
def test_rejects_credentials_and_invalid_json_at_any_depth(tmp_path, extra):
    with pytest.raises(ValueError):
        TaskRecoveryStore(tmp_path).create({**record(), **extra})
    assert not tuple(tmp_path.iterdir())


def test_update_failure_keeps_previous_durable_record(tmp_path, monkeypatch):
    store = TaskRecoveryStore(tmp_path)
    original = store.create(record())

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(task_recovery.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        store.update("task-one", state="completed")
    assert store.load("task-one") == original
    assert len(tuple(tmp_path.iterdir())) == 1


def test_rejects_duplicate_sources_and_entry_identities(tmp_path):
    value = record()
    value["sources"].append(deepcopy(value["sources"][0]))
    with pytest.raises(ValueError, match="source keys"):
        TaskRecoveryStore(tmp_path).create(value)
    value["sources"][1]["key"] = "second"
    with pytest.raises(ValueError, match="entry identities"):
        TaskRecoveryStore(tmp_path).create(value)


def test_corrupt_or_misnamed_records_fail_with_actionable_context(tmp_path):
    store = TaskRecoveryStore(tmp_path)
    store.create(record())
    path = next(tmp_path.glob("*.json"))
    path.write_text(json.dumps(record("wrong-task")), encoding="utf-8")
    with pytest.raises(ValueError, match="identity does not match"):
        store.load("task-one")
    with pytest.raises(ValueError, match="identity does not match"):
        store.list_records()
