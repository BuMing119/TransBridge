"""Checkpoint saves stay within legacy Windows path limits and remain atomic."""

from dataclasses import replace
import errno
import json
import os
from pathlib import Path, PureWindowsPath

import pytest

from transbridge.application.translation import (
    candidate_checkpoint_journal as journal,
    postprocess_checkpoint as postprocess,
)


@pytest.fixture
def windows_checkpoint_directory(tmp_path, monkeypatch):
    root = tmp_path / "postprocess"
    root.mkdir()
    # Reproduce the reported AppData layout independently of the test host's
    # temporary directory and whether its Python enables Windows long paths.
    windows_root = (
        PureWindowsPath("C:/Users/TestUser1/AppData/Roaming/TransBridge/data/task-store-v1/artifacts")
        / ("run-" + "a" * 48)
        / "ai-translation-resume"
        / "postprocess"
    )
    original_open = os.open

    def limited_open(path, flags, mode=0o777, **kwargs):
        candidate = Path(path)
        if candidate.is_relative_to(root):
            windows_path = windows_root / candidate.relative_to(root)
            if len(str(windows_path)) >= 260:
                raise FileNotFoundError(errno.ENOENT, "Windows path exceeds MAX_PATH", str(path))
        return original_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", limited_open)
    return root


def test_postprocess_save_updates_existing_checkpoint_under_windows_path_limit(windows_checkpoint_directory):
    root = windows_checkpoint_directory
    port = postprocess.FilesystemPostProcessCheckpointPort(root)
    first = postprocess.PostProcessCheckpoint("run", "owner", "a" * 64, revision=1)
    target = port._path(first.run_id)
    # Existing releases' durable filename and payload must remain readable.
    target.write_text(json.dumps(first.to_dict()), encoding="utf-8")
    assert port.load(first.run_id) == first

    updated = replace(first, revision=2, completed_phases=("polish",))
    port.save(updated)

    assert postprocess.FilesystemPostProcessCheckpointPort(root).load(first.run_id) == updated
    assert list(root.iterdir()) == [target]


def test_candidate_commit_head_updates_under_windows_path_limit(windows_checkpoint_directory):
    root = windows_checkpoint_directory
    target = root / f"translation-{'a' * 64}.json"
    target.write_text(json.dumps({"revision": 1}), encoding="utf-8")
    updated = {"revision": 2, "journal": "existing-journal.jsonl"}

    journal.publish_head(target, updated)

    assert json.loads(target.read_bytes()) == updated
    assert list(root.iterdir()) == [target]


@pytest.mark.parametrize("failure", ["fdopen", "fsync", "replace"])
@pytest.mark.parametrize("kind", ["postprocess", "candidate_head"])
def test_checkpoint_write_failure_preserves_existing_file_and_cleans_temporary(tmp_path, monkeypatch, kind, failure):
    if kind == "postprocess":
        port = postprocess.FilesystemPostProcessCheckpointPort(tmp_path)
        first = postprocess.PostProcessCheckpoint("run", "owner", "a" * 64, revision=1)
        port.save(first)
        target = port._path(first.run_id)
        updated = replace(first, revision=2)
        save = port.save
    else:
        target = tmp_path / "translation.json"
        journal.publish_head(target, {"revision": 1})
        updated = {"revision": 2}

        def save(value):
            journal.publish_head(target, value)

    before = target.read_bytes()

    def fail(*args, **kwargs):
        raise OSError(f"{failure} unavailable")

    monkeypatch.setattr(os, failure, fail)
    with pytest.raises(OSError, match=f"{failure} unavailable"):
        save(updated)

    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]
