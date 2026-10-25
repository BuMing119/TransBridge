"""Recover old translation progress without rewriting history or project data."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from transbridge.application.io.identity import EntryKey, EntryRevision, Provenance, SourceNamespace
from transbridge.application.translation import candidate_checkpoint_journal as journal
from transbridge.application.translation.candidate_checkpoint import (
    FilesystemTranslationCheckpointPort,
    TranslationCheckpoint,
)
from transbridge.application.translation.legacy_task_recovery import discover_legacy_records
from transbridge.application.translation.models import TranslationAction
from transbridge.application.translation.task_candidate_recovery import load_recovery_candidates, source_digest
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.application.translation.workload_models import CandidateTranslation


def _key(local="one", *, original=None):
    return EntryKey(SourceNamespace("plugin:test"), local, original)


def _input(key=None, **changes):
    key = key or _key()
    return {
        "entry_key": key.to_dict(),
        "original": "A place",
        "translation": "",
        "stage": 0,
        "context": "Place",
        "revision": 0,
        **changes,
    }


def _record(**changes):
    return {
        "task_id": "old-task",
        "created_at": "2026-10-24T19:43:13+08:00",
        "project_id": "project",
        "variant_id": "variant",
        "mode": "translate",
        "state": "running",
        "config_digest": "configured",
        "execution_runs": ["old-task"],
        "sources": [
            {
                "key": "source",
                "label": "Friendly display name",
                "translate_keys": [_key().to_dict()],
                "polish_keys": [],
                "inputs": [_input()],
                "checkpoint_stem": "Plugin",
            }
        ],
        **changes,
    }


def _config(runtime):
    return SimpleNamespace(get_ai_translator_dir=lambda stem: str(runtime / stem))


def _run(task="old-task", *, source="source", attempt=1, phase=""):
    return f"{task}-attempt-{attempt}-{source_digest(source)}" + (f"-{phase}" if phase else "")


def _checkpoint(runtime, *, run_id=None, text="候选译文", key=None, revision=0, schema=2, stem="Plugin"):
    run_id = run_id or _run()
    candidate = CandidateTranslation(
        run_id,
        key or _key(),
        EntryRevision(revision),
        TranslationAction.TRANSLATE,
        text,
        "a" * 64,
        1,
        "b" * 64,
        Provenance(run_id, "model", "translation"),
    )
    value = TranslationCheckpoint(run_id, "owner", "c" * 64, "d" * 64, revision=1, candidates=(candidate,))
    root = runtime / stem / "runtime-v2"
    port = FilesystemTranslationCheckpointPort(root)
    path = port._path(run_id)
    if schema == 1:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value.to_dict()), encoding="utf-8")
    else:
        port.save(value)
        path.with_suffix(".lock").unlink()
    return path, value


def _history(directory, record=None, **changes):
    record = record or _record()
    history = {
        "schema": "transbridge.task-history.v1",
        "run_id": record["task_id"],
        "created_at": record["created_at"],
        "project_id": record["project_id"],
        "variant_id": record["variant_id"],
        "state": record["state"],
        "sources": [
            {
                "key": source["key"],
                "label": source["label"],
                "snapshot": {
                    "entries": [
                        {
                            "entry_key": row["entry_key"],
                            "original": row["original"],
                            "before": row["translation"],
                            "candidate": "",
                            "before_revision": row["revision"],
                            "stage": row["stage"],
                            "context": row["context"],
                        }
                        for row in source["inputs"]
                    ]
                },
            }
            for source in record["sources"]
        ],
        **changes,
    }
    TaskHistoryStore(directory / "ai-task-history").save(history)
    return history


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("schema", [1, 2])
def test_readonly_checkpoint_load_never_creates_locks_or_migrates(tmp_path, schema):
    path, checkpoint = _checkpoint(tmp_path, schema=schema)
    before = _files(tmp_path)
    assert FilesystemTranslationCheckpointPort(path.parent).load_readonly(checkpoint.run_id) == checkpoint
    assert _files(tmp_path) == before


def test_readonly_missing_checkpoint_does_not_create_directory(tmp_path):
    root = tmp_path / "absent"
    assert FilesystemTranslationCheckpointPort(root).load_readonly("absent-run") is None
    assert not root.exists()


def test_readonly_retries_a_head_switch_instead_of_reporting_corruption(tmp_path, monkeypatch):
    path, checkpoint = _checkpoint(tmp_path)
    writer = FilesystemTranslationCheckpointPort(path.parent)
    newer = replace(checkpoint, revision=2, candidates=(replace(checkpoint.candidates[0], text="new generation"),))
    original = journal.read_records
    switched = False

    def switch_before_read(target, head):
        nonlocal switched
        if not switched:
            switched = True
            writer.save(newer)
        yield from original(target, head)

    monkeypatch.setattr(journal, "read_records", switch_before_read)
    assert FilesystemTranslationCheckpointPort(path.parent).load_readonly(checkpoint.run_id) == newer


def test_legacy_discovery_recovers_complete_scope_and_locates_storage_by_identity(tmp_path):
    project, runtime = tmp_path / "project", tmp_path / "ai_translator"
    original = _record()
    _history(project, original)
    _checkpoint(runtime)
    before = _files(tmp_path)
    (record,) = discover_legacy_records(project, runtime_root=runtime)
    assert record["legacy"] and record["config_policy"] == "current-settings"
    assert record["state"] == "interrupted" and record["execution_runs"] == ["old-task"]
    assert record["sources"][0]["inputs"] == original["sources"][0]["inputs"]
    assert record["sources"][0]["checkpoint_stems"] == ["Plugin"]
    candidates = load_recovery_candidates(record, "source", _config(runtime))
    assert candidates[_key()].text == "候选译文"
    assert _files(tmp_path) == before


def test_candidate_priority_uses_execution_then_attempt_then_phase(tmp_path):
    first, second = _key(), _key("two")
    record = _record(execution_runs=["old-task", "new-task"])
    record["sources"][0]["inputs"].append(_input(second))
    record["sources"][0]["translate_keys"].append(second.to_dict())
    _checkpoint(tmp_path, run_id=_run(attempt=20), text="older execution")
    _checkpoint(tmp_path, run_id=_run(task="new-task", phase="names"), text="name")
    _checkpoint(tmp_path, run_id=_run(task="new-task", phase="name-reuse"), text="reuse")
    _checkpoint(tmp_path, run_id=_run(task="new-task"), text="main")
    _checkpoint(tmp_path, run_id=_run(task="new-task", attempt=2, phase="names"), text="newer attempt")
    _checkpoint(tmp_path, run_id=_run(attempt=3, phase="names"), key=second, text="still useful")
    result = load_recovery_candidates(record, "source", _config(tmp_path))
    assert result[first].text == "newer attempt"
    assert result[second].text == "still useful"


def test_same_attempt_main_wins_over_name_phases_and_other_task_is_ignored(tmp_path):
    for phase in ("names", "name-reuse", ""):
        _checkpoint(tmp_path, run_id=_run(phase=phase), text=phase or "main")
    _checkpoint(tmp_path, run_id=_run(task="old-task-foreign"), text="unrelated")
    _checkpoint(tmp_path, run_id=_run(source="other-source"), text="wrong source")
    result = load_recovery_candidates(_record(), "source", _config(tmp_path))
    assert result[_key()].text == "main"


def test_second_resume_validates_candidate_against_that_execution_revision(tmp_path):
    record = _record(execution_runs=["old-task", "continued-task"])
    record["execution_inputs"] = {"continued-task": [{"key": "source", "inputs": [_input(revision=3)]}]}
    _checkpoint(tmp_path, text="old translation")
    _checkpoint(tmp_path, run_id=_run(task="continued-task"), text="continued translation", revision=3)
    result = load_recovery_candidates(record, "source", _config(tmp_path))
    assert result[_key()].text == "continued translation"
    assert result[_key()].before_revision == EntryRevision(3)


def test_completed_after_input_does_not_block_remaining_source_candidates(tmp_path):
    saved, remaining = _key(), _key("remaining")
    record = _record(execution_runs=["old-task", "continued-task"])
    source = record["sources"][0]
    source["inputs"].append(_input(remaining))
    source["translate_keys"].append(remaining.to_dict())
    record["execution_inputs"] = {
        "continued-task": [
            {
                "key": "source",
                "inputs": [
                    _input(saved, translation="Already applied", stage=2, revision=3),
                    _input(remaining, revision=3),
                ],
            }
        ]
    }
    _checkpoint(tmp_path, key=saved, text="Already applied")
    _checkpoint(tmp_path, run_id=_run(task="continued-task"), key=remaining, text="Remaining candidate", revision=3)
    result = load_recovery_candidates(record, "source", _config(tmp_path))
    assert result[saved].text == "Already applied"
    assert result[remaining].text == "Remaining candidate"
    assert result[remaining].before_revision == EntryRevision(3)


@pytest.mark.parametrize(
    "field,value", [("original", "Changed original"), ("translation", "Changed"), ("stage", 2), ("context", "Changed")]
)
def test_execution_revision_does_not_authorize_different_input_content(tmp_path, field, value):
    record = _record(execution_runs=["continued-task"])
    record["execution_inputs"] = {
        "continued-task": [{"key": "source", "inputs": [_input(revision=3, **{field: value})]}]
    }
    _checkpoint(tmp_path, run_id=_run(task="continued-task"), revision=3)
    with pytest.raises(ValueError, match="内容与原始范围不一致"):
        load_recovery_candidates(record, "source", _config(tmp_path))


def test_execution_revision_mismatch_is_still_rejected(tmp_path):
    record = _record(execution_runs=["continued-task"])
    record["execution_inputs"] = {"continued-task": [{"key": "source", "inputs": [_input(revision=3)]}]}
    _checkpoint(tmp_path, run_id=_run(task="continued-task"), revision=4)
    with pytest.raises(ValueError, match="版本或翻译动作不一致"):
        load_recovery_candidates(record, "source", _config(tmp_path))


@pytest.mark.parametrize("change", ["revision", "scope", "action"])
def test_rejects_candidate_without_matching_original_scope_evidence(tmp_path, change):
    path, value = _checkpoint(tmp_path, schema=1, revision=1 if change == "revision" else 0)
    if change == "action":
        value = replace(value, candidates=(replace(value.candidates[0], action=TranslationAction.POLISH),))
        path.write_text(json.dumps(value.to_dict()), encoding="utf-8")
    record = _record()
    if change == "scope":
        record["sources"][0]["translate_keys"] = []
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="范围|版本|动作"):
        load_recovery_candidates(record, "source", _config(tmp_path))
    assert _files(tmp_path) == before


def test_changed_original_qualified_identity_is_rejected(tmp_path):
    key = _key(original="Old original")
    record = _record()
    record["sources"][0]["inputs"] = [_input(key)]
    record["sources"][0]["translate_keys"] = [key.to_dict()]
    with pytest.raises(ValueError, match="原文不一致"):
        load_recovery_candidates(record, "source", _config(tmp_path))


def test_corrupt_journal_remains_discoverable_but_cannot_become_empty_progress(tmp_path):
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    _history(project)
    path, _ = _checkpoint(runtime)
    head = json.loads(path.read_bytes())
    journal = path.parent / head["journal"]
    journal.write_bytes(journal.read_bytes()[:-7])
    (record,) = discover_legacy_records(project, runtime_root=runtime)
    before = _files(tmp_path)
    with pytest.raises(ValueError, match="读取恢复检查点失败"):
        load_recovery_candidates(record, "source", _config(runtime))
    assert _files(tmp_path) == before


def test_corrupt_head_is_an_explicit_continuation_error(tmp_path):
    path, _ = _checkpoint(tmp_path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="身份损坏"):
        load_recovery_candidates(_record(), "source", _config(tmp_path))


def test_bad_checkpoint_filename_does_not_cross_task_boundary(tmp_path):
    path, _ = _checkpoint(tmp_path)
    path.rename(path.with_name("translation-" + "e" * 64 + ".json"))
    with pytest.raises(ValueError, match="文件名不一致"):
        load_recovery_candidates(_record(), "source", _config(tmp_path))


@pytest.mark.parametrize("state", ["cancelled", "completed"])
def test_final_or_cancelled_history_is_never_reoffered(tmp_path, state):
    _history(tmp_path, state=state)
    _checkpoint(tmp_path)
    assert discover_legacy_records(tmp_path, runtime_root=tmp_path) == ()


def test_old_mixed_or_missing_input_scope_is_not_guessed(tmp_path):
    record = _record()
    record["sources"][0]["inputs"][0]["translation"] = "已有译文"
    _history(tmp_path, record)
    _checkpoint(tmp_path)
    assert discover_legacy_records(tmp_path, runtime_root=tmp_path) == ()


def test_native_manifest_takes_precedence_without_rewriting_legacy_report(tmp_path):
    _history(tmp_path)
    _checkpoint(tmp_path)
    TaskRecoveryStore(tmp_path / "ai-task-recovery").create(_record())
    before = _files(tmp_path)
    assert discover_legacy_records(tmp_path, runtime_root=tmp_path) == ()
    assert _files(tmp_path) == before


def test_no_saved_candidate_is_a_valid_empty_new_task(tmp_path):
    assert load_recovery_candidates(_record(), "source", _config(tmp_path)) == {}
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("stem", ["../elsewhere", "D:/outside", "..", "sub\\file"])
def test_recovery_checkpoint_directory_cannot_escape_plugin_storage(tmp_path, stem):
    record = deepcopy(_record())
    record["sources"][0]["checkpoint_stem"] = stem
    with pytest.raises(ValueError, match="目录身份无效"):
        load_recovery_candidates(record, "source", _config(tmp_path))


def test_load_accepts_original_non_attempt_source_ids(tmp_path):
    run_id = f"old-task-{hashlib.sha256(b'source').hexdigest()[:20]}"
    _checkpoint(tmp_path, run_id=run_id)
    assert load_recovery_candidates(_record(), "source", _config(tmp_path))[_key()].run_id == run_id
