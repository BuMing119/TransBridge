"""Actual filesystem evidence and partial-failure behavior of file undo."""

import json
import os
from pathlib import Path

import pytest

from transbridge.application.io.file_undo import FileUndoAdapter, FileUndoError


def test_restore_and_delete_only_explicit_outputs(tmp_path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    untouched = tmp_path / "user.txt"
    old.write_bytes(b"original")
    undo = FileUndoAdapter([old, new], backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    old.write_bytes(b"published")
    new.write_bytes(b"created")
    untouched.write_bytes(b"user edit")
    receipt = json.loads(json.dumps(undo.capture_after(receipt)))
    assert undo.preflight(receipt) == []
    result = undo.apply(receipt)
    assert result["status"] == "completed"
    assert old.read_bytes() == b"original"
    assert not new.exists()
    assert untouched.read_bytes() == b"user edit"
    assert undo.apply(receipt)["status"] == "conflict"


def test_one_conflict_rejects_every_file_before_mutation(tmp_path):
    files = [tmp_path / "a", tmp_path / "b"]
    for path in files:
        path.write_bytes(b"before")
    undo = FileUndoAdapter(files, backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    for path in files:
        path.write_bytes(b"after")
    receipt = undo.capture_after(receipt)
    files[1].write_bytes(b"later user change")
    result = undo.apply(receipt)
    assert result["status"] == "conflict"
    assert result["restored"] == []
    assert files[0].read_bytes() == b"after"
    assert files[1].read_bytes() == b"later user change"


def test_corrupt_backup_refuses_to_modify_output(tmp_path):
    path = tmp_path / "output"
    path.write_bytes(b"before")
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    path.write_bytes(b"after")
    receipt = undo.capture_after(receipt)
    (undo.backup_root / receipt["files"][0]["backup"]).write_bytes(b"corrupt")
    assert undo.apply(receipt)["status"] == "conflict"
    assert path.read_bytes() == b"after"


def test_partial_failure_reports_completed_and_remaining(tmp_path, monkeypatch):
    files = [tmp_path / "a", tmp_path / "b"]
    for path in files:
        path.write_bytes(b"before")
    undo = FileUndoAdapter(files, backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    for path in files:
        path.write_bytes(b"after")
    receipt = undo.capture_after(receipt)
    actual = os.replace

    def replace(source, target):
        if Path(target) == files[1]:
            raise OSError("disk refused second restore")
        return actual(source, target)

    monkeypatch.setattr(os, "replace", replace)
    result = undo.apply(receipt)
    assert result["status"] == "partial"
    assert result["restored"] == [str(files[0])]
    assert result["remaining"] == [str(files[1])]
    assert files[0].read_bytes() == b"before"
    assert files[1].read_bytes() == b"after"
    assert not list(tmp_path.glob(".transbridge-undo-*"))


def test_receipt_cannot_expand_authorized_paths(tmp_path):
    path = tmp_path / "output"
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    receipt["files"][0]["path"] = str(tmp_path / "other")
    with pytest.raises(FileUndoError, match="authorized"):
        undo.apply(receipt)


def test_parent_replacement_detected_even_with_original_output_inode(tmp_path):
    parent = tmp_path / "output"
    parent.mkdir()
    path = parent / "file"
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    path.write_bytes(b"after")
    receipt = undo.capture_after(receipt)
    original = tmp_path / "renamed"
    parent.rename(original)
    parent.mkdir()
    (original / "file").rename(path)
    assert undo.apply(receipt)["status"] == "conflict"
    assert path.read_bytes() == b"after"


def test_absent_targets_and_directories_are_never_created_or_deleted(tmp_path):
    path = tmp_path / "missing" / "output"
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_after(undo.capture_before())
    assert undo.apply(receipt)["status"] == "completed"
    assert not path.parent.exists()


def test_backup_path_escape_rejected(tmp_path):
    path = tmp_path / "output"
    path.write_bytes(b"before")
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_after(undo.capture_before())
    receipt["files"][0]["backup"] = "../output"
    assert undo.apply(receipt)["status"] == "conflict"
    assert path.read_bytes() == b"before"


def test_unsealed_receipt_is_not_an_undo_authorization(tmp_path):
    path = tmp_path / "output"
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    path.write_bytes(b"publication")
    assert undo.apply(receipt)["status"] == "conflict"
    assert path.read_bytes() == b"publication"


def test_reject_links_and_hard_links(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"user data")
    link = tmp_path / "link"
    try:
        os.link(target, link)
    except OSError:
        pytest.skip("filesystem does not support hard links")
    undo = FileUndoAdapter([link], backup_root=tmp_path / "backups")
    with pytest.raises(FileUndoError, match="hard links"):
        undo.capture_before()


@pytest.mark.parametrize("path", ["relative/file", "../file"])
def test_relative_authorizations_rejected(tmp_path, path):
    with pytest.raises(FileUndoError, match="absolute"):
        FileUndoAdapter([path], backup_root=tmp_path / "backups")


def test_backup_directory_identity_is_checked(tmp_path):
    path = tmp_path / "output"
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_before()
    path.write_bytes(b"after")
    receipt = undo.capture_after(receipt)
    undo.backup_root.rename(tmp_path / "old-backups")
    undo.backup_root.mkdir()
    assert undo.apply(receipt)["status"] == "conflict"


def test_symlink_parent_is_rejected(tmp_path):
    directory = tmp_path / "real"
    directory.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks require additional OS privileges")
    with pytest.raises(FileUndoError, match="links/reparse"):
        FileUndoAdapter([link / "file"], backup_root=tmp_path / "backups")


def test_no_publication_keeps_original_file_identity(tmp_path):
    path = tmp_path / "output"
    path.write_bytes(b"unchanged")
    identity = path.stat().st_ino
    undo = FileUndoAdapter([path], backup_root=tmp_path / "backups")
    receipt = undo.capture_after(undo.capture_before())
    assert undo.apply(receipt)["status"] == "completed"
    assert path.stat().st_ino == identity
