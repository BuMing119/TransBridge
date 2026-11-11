from dataclasses import replace
import json
from pathlib import Path

import pytest

from tests.contracts.translation.test_postprocess_execution import _collection, _input
from tests.path_fixtures import deep_directory
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import StagePolicy
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.io.publish.filesystem import OsPublishFilesystem
from transbridge.application.translation import (
    FilesystemPostProcessCheckpointPort,
    FilesystemTranslationCheckpointPort,
    PostProcessCheckpoint,
    PostProcessExecutionService,
    PostProcessStageOutcome,
    PostProcessWorkload,
    TranslationCheckpoint,
)


@pytest.mark.parametrize("depth", [145, 169, 265])
def test_candidate_journal_saves_and_reopens_at_long_paths(tmp_path, depth):
    root = deep_directory(tmp_path, depth)
    port = FilesystemTranslationCheckpointPort(root)
    saved = TranslationCheckpoint("run", "owner", "a" * 64, "b" * 64, revision=1)
    port.save(saved)
    assert FilesystemTranslationCheckpointPort(root).load_readonly("run") == saved
    newer = replace(saved, revision=2)
    port.save(newer)
    assert FilesystemTranslationCheckpointPort(root).load("run") == newer


@pytest.mark.parametrize("kind", ["candidate", "phase"])
def test_long_path_existing_snapshot_keeps_identity_and_supports_update(tmp_path, kind):
    root = deep_directory(tmp_path, 265)
    if kind == "candidate":
        port = FilesystemTranslationCheckpointPort(root)
        saved = TranslationCheckpoint("run", "owner", "a" * 64, "b" * 64, revision=1)
    else:
        port = FilesystemPostProcessCheckpointPort(root)
        saved = PostProcessCheckpoint("run", "owner", "a" * 64, revision=1)
    target = Path(OsPublishFilesystem().canonicalize(str(port._path("run"))))
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(saved.to_dict()), encoding="utf-8")
    original_name = target.name
    assert port.load("run") == saved
    newer = replace(saved, revision=2)
    port.save(newer)
    assert type(port)(root).load("run") == newer
    assert port._path("run").name == original_name


def test_phase_checkpoint_cleans_failed_temporary_and_preserves_previous_value(tmp_path, monkeypatch):
    from transbridge.application.translation import postprocess_checkpoint as module

    port = FilesystemPostProcessCheckpointPort(deep_directory(tmp_path, 265))
    saved = PostProcessCheckpoint("run", "owner", "a" * 64, revision=1)
    port.save(saved)

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        port.save(replace(saved, revision=2))
    assert port.load("run") == saved
    assert not list(port._path("run").parent.glob("*.tmp"))


def test_reviewed_text_and_commit_evidence_both_persist_at_long_path(tmp_path):
    entry = _input()
    port = FilesystemTranslationCheckpointPort(deep_directory(tmp_path, 169))

    def reviewed(candidates):
        return PostProcessStageOutcome("proofread", tuple(c.with_text("Reviewed", "proofread") for c in candidates))

    result = PostProcessExecutionService(
        PostProcessWorkload((reviewed,), stage_policy=StagePolicy(), stage_names=("proofread",))
    ).execute(
        run_id="run",
        entries=(entry,),
        collection=_collection(entry),
        context=RequestContext(
            "owner", run_id="run", permissions=frozenset({"entry.translation.write", "entry.stage.write"})
        ),
        commit_guard=ImmediateCommitGuard("run"),
        commit_checkpoint=port,
    )
    assert result.outcome is OperationOutcome.COMPLETED
    saved = FilesystemTranslationCheckpointPort(port._root).load_readonly("run")
    assert result.commit_result.value["commit_id"] in saved.committed_candidate_ids
