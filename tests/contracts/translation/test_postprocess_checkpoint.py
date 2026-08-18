"""Post-process checkpoint contract: atomicity, revision, identity and resume."""

from __future__ import annotations

import hashlib
import json

import pytest

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace, StagePolicy
from transbridge.application.translation import (
    FilesystemPostProcessCheckpointPort,
    InMemoryPostProcessCheckpointPort,
    PostProcessCheckpoint,
    PostProcessStageOutcome,
    PostProcessWorkload,
    TranslationInput,
)

KEY = EntryKey(SourceNamespace("fixture"), "one")


def _entry(*, stage: int = 1, revision: int = 4) -> TranslationInput:
    return TranslationInput(KEY, EntryRevision(revision), "source", "draft", stage)


def _make(phase: str, transformer):
    def call(candidates):
        return PostProcessStageOutcome(phase, tuple(transformer(candidate) for candidate in candidates))

    setattr(call, "phase", phase)
    return call


def _refine(candidate):
    return candidate.with_text(f"refined:{candidate.text}", "refine")


def _polish(candidate):
    return candidate.with_text(f"polished:{candidate.text}", "polish")


def _full_workload(checkpoint_port, extra_phase=None):
    stages = (_make("refine", _refine), _make("polish", _polish))
    names = ["refine", "polish"]
    if extra_phase is not None:
        stages = (*stages, _make(extra_phase, lambda candidate: candidate))
        names.append(extra_phase)
    return PostProcessWorkload(
        stages,
        stage_policy=StagePolicy(),
        stage_names=tuple(names),
        checkpoint_port=checkpoint_port,
    )


def test_checkpoint_persists_stage_and_candidate_hash_and_resumes_without_replay() -> None:
    port = InMemoryPostProcessCheckpointPort()
    calls: list[int] = []

    def arbitrate(candidates):
        calls.append(len(candidates))
        return PostProcessStageOutcome("arbitrate", candidates)

    workload = PostProcessWorkload(
        (_make("refine", _refine), _make("polish", _polish), arbitrate),
        stage_policy=StagePolicy(),
        stage_names=("refine", "polish", "arbitrate"),
        checkpoint_port=port,
    )
    result = workload.run("run-r1", (_entry(),), owner_id="owner")

    assert result.outcome.value == "completed"
    checkpoint = port.load("run-r1")
    assert checkpoint is not None
    assert checkpoint.completed_phases == ("refine", "polish", "arbitrate")
    entry = checkpoint.entries[0]
    assert entry.phase == "arbitrate"
    assert entry.text == "polished:refined:draft"
    assert entry.candidate_hash() == entry.candidate_sha256

    calls.clear()
    resumed = workload.run("run-r1", (_entry(),), owner_id="owner", resume_after_phase="polish")

    assert resumed.outcome.value == "completed"
    assert calls == [1]
    assert [candidate.text for candidate in resumed.value.candidates] == ["polished:refined:draft"]


def test_checkpoint_identity_is_bound_to_owner_and_input_fingerprint() -> None:
    port = InMemoryPostProcessCheckpointPort()
    workload = _full_workload(port, extra_phase="arbitrate")
    workload.run("run-r2", (_entry(),), owner_id="owner-a")

    with pytest.raises(ValueError, match="identity"):
        _full_workload(port, extra_phase="arbitrate").run(
            "run-r2", (_entry(),), owner_id="owner-b", resume_after_phase="polish"
        )
    with pytest.raises(ValueError, match="identity"):
        _full_workload(port, extra_phase="arbitrate").run(
            "run-r2", (_entry(stage=9),), owner_id="owner-a", resume_after_phase="polish"
        )


def test_filesystem_checkpoint_is_atomic_and_revision_monotonic(tmp_path) -> None:
    port = FilesystemPostProcessCheckpointPort(tmp_path / "ckpt")
    workload = _full_workload(port)
    result = workload.run("run-fs", (_entry(),), owner_id="owner")

    assert result.outcome.value == "completed"
    first = port.load("run-fs")
    assert first is not None
    assert first.revision == 2
    assert len(list((tmp_path / "ckpt").iterdir())) == 1

    advanced = first.advance(phase="done", entries=first.entries)
    port.save(advanced)
    assert port.load("run-fs").revision == 3
    with pytest.raises(ValueError, match="revision"):
        port.save(first)

    digest = hashlib.sha256(b"run-fs").hexdigest()
    target = tmp_path / "ckpt" / f"postprocess-{digest}.json"
    target.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        port.load("run-fs")


def test_resume_from_unknown_phase_is_rejected() -> None:
    port = InMemoryPostProcessCheckpointPort()
    _full_workload(port).run("run-r3", (_entry(),), owner_id="owner")

    with pytest.raises(ValueError, match="does not match"):
        _full_workload(port, extra_phase="extra").run(
            "run-r3", (_entry(),), owner_id="owner", resume_after_phase="extra"
        )
    with pytest.raises(ValueError, match="does not match"):
        _full_workload(port).run("run-r3", (_entry(),), owner_id="owner", resume_after_phase="arbitrate")


def test_checkpoint_round_trips_through_json_without_loss() -> None:
    port = InMemoryPostProcessCheckpointPort()
    _full_workload(port, extra_phase="arbitrate").run("run-r4", (_entry(),), owner_id="owner")
    checkpoint = port.load("run-r4")
    restored = PostProcessCheckpoint.from_dict(json.loads(json.dumps(checkpoint.to_dict())))
    assert restored == checkpoint