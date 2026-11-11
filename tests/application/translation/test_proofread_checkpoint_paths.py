"""Real SQLite persistence at Windows AppData-sized checkpoint paths."""

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from tests.application.translation.test_proofread_checkpoint import Client, candidate, store
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.filesystem_paths import sqlite_uri


@pytest.fixture
def long_root(tmp_path):
    root = tmp_path / "校对 100% #&" / "ai-proofread-resume"
    # Include reserved URI characters and reproduce the reported 292-character
    # filename independently of pytest's temporary-directory length.
    while len(str(store(root).path)) < 292:
        root /= "x" * min(80, 292 - len(str(store(root).path)))
    root.mkdir(parents=True)
    return root


@pytest.mark.parametrize("task_id", [None, "existing-task"])
def test_long_checkpoint_path_saves_reopens_and_resumes_without_repeating_requests(long_root, task_id):
    checkpoint = store(long_root, task_id=task_id)
    first = ProofreadStage(Client(), checkpoint=checkpoint).run((candidate(1),))

    assert first.candidates[0].text == "您好"
    assert checkpoint.saved_count() == 1
    assert checkpoint.read()
    resumed_client = Client()
    resumed = ProofreadStage(resumed_client, checkpoint=store(long_root, task_id=task_id)).run((candidate(1),))
    assert resumed.candidates[0].text == "您好"
    assert not resumed_client.calls
    assert not tuple(checkpoint.path.parent.glob("*-journal"))


@pytest.mark.parametrize("task_id", [None, "existing-task"])
def test_existing_database_keeps_its_filename_and_is_reusable_at_long_path(tmp_path, long_root, task_id):
    old_root = tmp_path / "old"
    original = store(old_root, task_id=task_id)
    ProofreadStage(Client(), checkpoint=original).run((candidate(1),))
    old_layout = Path(*original.path.parts[-(3 if task_id is not None else 1) :])
    target = long_root / old_layout
    native_target = Path("\\\\?\\" + str(target)) if os.name == "nt" else target
    native_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original.path, native_target)
    before = native_target.read_bytes()

    checkpoint = store(long_root, task_id=task_id)
    client = Client()
    result = ProofreadStage(client, checkpoint=checkpoint).run((candidate(1),))

    assert checkpoint.path.samefile(native_target)
    assert checkpoint.saved_count() == 1
    assert result.candidates[0].text == "您好"
    assert not client.calls
    assert native_target.read_bytes() == before


@pytest.mark.parametrize("corruption", ["database", "payload"])
def test_long_path_corruption_backup_preserves_original_and_allows_recovery(long_root, corruption):
    checkpoint = store(long_root, task_id="task")
    checkpoint.path.parent.mkdir(parents=True)
    if corruption == "database":
        checkpoint.path.write_bytes(b"broken database")
    else:
        checkpoint.write([("broken", {"text": "", "terms": "invalid"})])
    before = checkpoint.path.read_bytes()

    result = ProofreadStage(Client(), checkpoint=checkpoint).run((candidate(1),))

    assert result.candidates[0].text == "您好"
    assert checkpoint.saved_count() == 1
    backups = tuple(checkpoint.path.parent.glob("corrupt-*.sqlite3"))
    assert len(backups) == 1
    if corruption == "database":
        assert backups[0].read_bytes() == before
    else:
        with closing(sqlite3.connect(sqlite_uri(backups[0]), uri=True)) as db:
            assert json.loads(db.execute("SELECT payload FROM candidates").fetchone()[0]) == {
                "text": "",
                "terms": "invalid",
            }


def test_long_path_readonly_connection_never_creates_or_writes_a_checkpoint(long_root):
    checkpoint = store(long_root)
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(sqlite_uri(checkpoint.path, mode="ro"), uri=True)
    assert not checkpoint.path.exists()

    checkpoint.write([("first", {"text": "译文", "terms": "a" * 64})])
    with closing(sqlite3.connect(sqlite_uri(checkpoint.path, mode="ro"), uri=True)) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM candidates")
    assert "first" in checkpoint.read()
