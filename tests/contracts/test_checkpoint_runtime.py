from __future__ import annotations

import json
from pathlib import Path
import statistics
import time

import pytest

from transbridge.application.tasks import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointCorruptError,
    CheckpointExpectation,
    CheckpointFrontier,
    CheckpointFutureSchemaError,
    CheckpointMismatchError,
    CheckpointRecord,
    CheckpointRevisionError,
    FilesystemCheckpointPort,
    LegacyCheckpointError,
    OwnerRef,
)
import transbridge.application.tasks.checkpoint_fs as checkpoint_fs


def owner(**changes) -> OwnerRef:
    values = {
        "owner_id": "owner-1",
        "entrypoint": "agent",
        "project_id": "project-1",
        "variant_id": "variant-1",
        "session_id": "session-1",
    }
    values.update(changes)
    return OwnerRef(**values)


def record(*, revision: int = 3, completed_commit_ids=frozenset({"commit-1"})) -> CheckpointRecord:
    return CheckpointRecord(
        run_id="run-1",
        owner=owner(),
        spec_fingerprint="sha256:spec",
        input_fingerprint="sha256:input",
        revision=revision,
        frontier=CheckpointFrontier(
            ready=("node-c",),
            running=("node-b",),
            completed=("node-a",),
        ),
        completed_entry_keys=("entry:a",),
        completed_actions=("translate:entry:a",),
        completed_commit_ids=completed_commit_ids,
        candidate_refs=("candidate:a",),
        branch_decisions=(("condition-a", "node-c"),),
        loop_counters=(("loop-a", 2),),
        hitl_results=(("confirm-a", "continue"),),
        graph_results=(("node-a", {"success": True, "data": {"value": 1}}),),
    )


def expectation(**changes) -> CheckpointExpectation:
    values = {
        "run_id": "run-1",
        "owner": owner(),
        "spec_fingerprint": "sha256:spec",
        "input_fingerprint": "sha256:input",
    }
    values.update(changes)
    return CheckpointExpectation(**values)


def test_atomic_roundtrip_preserves_full_versioned_record(tmp_path):
    port = FilesystemCheckpointPort(tmp_path)
    source = record()
    port.save(source)

    restored = port.load(source.run_id, expected=expectation())

    assert restored == source
    assert restored.schema_version == CHECKPOINT_SCHEMA_VERSION
    assert list(tmp_path.rglob("*.tmp")) == []


@pytest.mark.skipif(checkpoint_fs.os.name != "nt", reason="Windows sharing race contract")
def test_atomic_replace_retries_transient_windows_sharing_violation(tmp_path, monkeypatch):
    real_replace = checkpoint_fs.os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "transient sharing violation")
        real_replace(source, target)

    monkeypatch.setattr(checkpoint_fs.os, "replace", flaky_replace)
    port = FilesystemCheckpointPort(tmp_path)
    port.save(record())

    assert attempts == 3
    assert port.load("run-1") == record()


@pytest.mark.parametrize(
    "changed",
    [
        {"run_id": "run-other"},
        {"owner": owner(session_id="other-session")},
        {"spec_fingerprint": "sha256:other-spec"},
        {"input_fingerprint": "sha256:other-input"},
    ],
)
def test_strict_resume_identity_rejects_drift(tmp_path, changed):
    port = FilesystemCheckpointPort(tmp_path)
    port.save(record())
    with pytest.raises(CheckpointMismatchError) as captured:
        port.load("run-1", expected=expectation(**changed))
    assert captured.value.code in {"checkpoint_identity_mismatch", "checkpoint_run_id_mismatch"}


def test_corrupt_future_and_legacy_checkpoints_are_explicitly_rejected(tmp_path):
    port = FilesystemCheckpointPort(tmp_path)
    path = port.path_for("run-1")
    path.parent.mkdir(parents=True)

    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(CheckpointCorruptError) as corrupt:
        port.load("run-1")
    assert corrupt.value.code == "checkpoint_invalid_json"

    future = record().to_dict()
    future["schema_version"] = CHECKPOINT_SCHEMA_VERSION + 1
    path.write_text(json.dumps(future), encoding="utf-8")
    with pytest.raises(CheckpointFutureSchemaError) as future_error:
        port.load("run-1")
    assert future_error.value.code == "checkpoint_future_schema"

    path.write_text(json.dumps({"graph_id": "legacy"}), encoding="utf-8")
    with pytest.raises(LegacyCheckpointError) as legacy:
        port.load("run-1")
    assert legacy.value.code == "checkpoint_legacy_non_atomic"


@pytest.mark.parametrize(
    "stage",
    ["before_temp_write", "after_temp_write", "after_temp_fsync", "before_replace"],
)
def test_fault_before_replace_preserves_last_good_checkpoint(tmp_path, stage):
    stable_port = FilesystemCheckpointPort(tmp_path)
    stable_port.save(record(revision=1))

    def crash(point: str, _path: Path) -> None:
        if point == stage:
            raise RuntimeError(f"crash:{stage}")

    crashing_port = FilesystemCheckpointPort(tmp_path, fault_injector=crash)
    with pytest.raises(RuntimeError, match="crash"):
        crashing_port.save(record(revision=2))

    assert stable_port.load("run-1").revision == 1
    assert list(tmp_path.rglob("*.tmp")) == []


def test_fault_after_replace_leaves_new_checkpoint_valid(tmp_path):
    stable_port = FilesystemCheckpointPort(tmp_path)
    stable_port.save(record(revision=1))

    def crash(point: str, _path: Path) -> None:
        if point == "after_replace":
            raise RuntimeError("power loss after replace")

    with pytest.raises(RuntimeError, match="power loss"):
        FilesystemCheckpointPort(tmp_path, fault_injector=crash).save(record(revision=2))

    assert stable_port.load("run-1").revision == 2


def test_load_fault_is_propagated_not_treated_as_no_checkpoint(tmp_path):
    stable_port = FilesystemCheckpointPort(tmp_path)
    stable_port.save(record())

    def crash(point: str, _path: Path) -> None:
        if point == "before_load":
            raise OSError("storage offline")

    with pytest.raises(OSError, match="storage offline"):
        FilesystemCheckpointPort(tmp_path, fault_injector=crash).load("run-1")


def test_repeated_resume_commit_id_is_idempotent():
    source = record()
    assert not source.accepts_commit("commit-1")
    first = source.mark_committed("commit-2")
    second = first.mark_committed("commit-2")
    assert first is second
    assert first.completed_commit_ids == frozenset({"commit-1", "commit-2"})


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"revision": 1.5},
        {"completed_commit_ids": frozenset({""})},
        {"completed_entry_keys": ("",)},
        {"completed_actions": ("",)},
        {"candidate_refs": ("",)},
        {"loop_counters": (("loop", True),)},
        {"loop_counters": (("loop", 1.5),)},
    ],
)
def test_direct_record_construction_strictly_rejects_invalid_values(changes):
    values = record().to_dict()
    values.update(changes)
    values.pop("schema_version", None)
    if "owner" in values and isinstance(values["owner"], dict):
        values["owner"] = owner()
    frontier = values.get("frontier")
    if isinstance(frontier, dict):
        values["frontier"] = CheckpointFrontier(
            tuple(frontier["ready"]),
            tuple(frontier["running"]),
            tuple(frontier["completed"]),
        )
    for field_name in ("completed_entry_keys", "completed_actions", "candidate_refs"):
        if isinstance(values.get(field_name), list):
            values[field_name] = tuple(values[field_name])
    if isinstance(values.get("completed_commit_ids"), list):
        values["completed_commit_ids"] = frozenset(values["completed_commit_ids"])
    for field_name in ("branch_decisions", "loop_counters", "hitl_results", "graph_results"):
        if isinstance(values.get(field_name), dict):
            values[field_name] = tuple(values[field_name].items())
    with pytest.raises((TypeError, ValueError)):
        CheckpointRecord(**values)


def test_graph_result_payload_is_deeply_frozen_and_to_dict_is_independent():
    mutable = {"nested": {"items": [1, 2]}}
    source = record()
    source = CheckpointRecord(**{
        **source.to_dict(),
        "owner": owner(),
        "frontier": source.frontier,
        "completed_entry_keys": source.completed_entry_keys,
        "completed_actions": source.completed_actions,
        "completed_commit_ids": source.completed_commit_ids,
        "candidate_refs": source.candidate_refs,
        "branch_decisions": source.branch_decisions,
        "loop_counters": source.loop_counters,
        "hitl_results": source.hitl_results,
        "graph_results": (("node-a", mutable),),
    })
    mutable["nested"]["items"].append(3)
    first = source.to_dict()
    first["graph_results"]["node-a"]["nested"]["items"].append(4)
    assert source.to_dict()["graph_results"]["node-a"] == {"nested": {"items": [1, 2]}}


def test_revision_regression_and_equal_revision_conflict_are_rejected(tmp_path):
    port = FilesystemCheckpointPort(tmp_path)
    port.save(record(revision=5))
    with pytest.raises(CheckpointRevisionError) as regression:
        port.save(record(revision=4))
    assert regression.value.code == "checkpoint_revision_regression"

    conflicting = CheckpointRecord(**{
        **record(revision=5).to_dict(),
        "owner": owner(),
        "frontier": CheckpointFrontier(("other",), (), ()),
        "completed_entry_keys": (),
        "completed_actions": (),
        "completed_commit_ids": frozenset(),
        "candidate_refs": (),
        "branch_decisions": (),
        "loop_counters": (),
        "hitl_results": (),
        "graph_results": (),
    })
    with pytest.raises(CheckpointRevisionError) as conflict:
        port.save(conflicting)
    assert conflict.value.code == "checkpoint_revision_conflict"


def test_concurrent_late_lower_revision_cannot_overwrite_newer_record(tmp_path):
    import threading

    port_a = FilesystemCheckpointPort(tmp_path)
    port_b = FilesystemCheckpointPort(tmp_path)
    barrier = threading.Barrier(3)
    errors = []

    def save(value):
        barrier.wait()
        try:
            value[0].save(record(revision=value[1]))
        except CheckpointRevisionError as exc:
            errors.append(exc.code)

    threads = [
        threading.Thread(target=save, args=((port_a, 10),)),
        threading.Thread(target=save, args=((port_b, 9),)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(2)

    assert port_a.load("run-1").revision == 10
    assert errors in ([], ["checkpoint_revision_regression"])


def test_100k_commit_dedup_update_p95_is_under_100ms():
    source = record(completed_commit_ids=frozenset(f"commit-{index}" for index in range(100_000)))
    durations = []
    for index in range(20):
        started = time.perf_counter()
        updated = source.mark_committed(f"new-{index}")
        durations.append((time.perf_counter() - started) * 1000)
        assert updated.accepts_commit(f"new-{index}") is False
    p95 = statistics.quantiles(durations, n=20)[18]
    assert p95 < 100
