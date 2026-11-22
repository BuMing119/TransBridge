import os
import shutil
import sqlite3
import sys

import pytest

from transbridge.config import storage_migration as migration
from transbridge.config.storage_location import active_storage_root, default_data_path, default_progress_path


@pytest.fixture
def locations(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    data, progress = default_data_path(), default_progress_path()
    data.mkdir(parents=True)
    (data / "empty").mkdir()
    (data / "settings.ini").write_text("[UI]", encoding="utf-8")
    db = sqlite3.connect(data / "tasks.sqlite3")
    try:
        db.execute("create table tasks (id integer)")
        db.execute("insert into tasks values (42)")
        db.commit()
    finally:
        db.close()
    run = progress / "run-123"
    run.mkdir(parents=True)
    (run / "进度.json").write_bytes(b"progress")
    return data, progress, tmp_path / "new" / "TransBridge"


def test_moves_both_trees_including_database_then_moves_again(locations):
    data, progress, target = locations
    events = []
    assert migration.migrate_storage(target, events.append) == ()
    assert active_storage_root() == target
    assert not data.exists() and not progress.exists()
    assert any("复制第 3/3 个文件" in event for event in events)
    assert any("核对第 3/3 个文件" in event for event in events)
    assert (target / "data" / "empty").is_dir()
    assert (target / "翻译进度" / "run-123" / "进度.json").read_bytes() == b"progress"
    db = sqlite3.connect(target / "data" / "tasks.sqlite3")
    try:
        assert db.execute("select id from tasks").fetchall() == [(42,)]
    finally:
        db.close()

    extra = target / "my-note.txt"
    extra.write_text("keep")
    second = target.parents[1] / "second" / "TransBridge"
    assert migration.migrate_storage(second) == ()
    assert active_storage_root() == second
    assert list(target.iterdir()) == [extra]
    assert (second / "data" / "tasks.sqlite3").is_file()


@pytest.mark.parametrize("where", ["inside_data", "inside_progress", "parent", "fixed", "occupied", "space"])
def test_rejects_invalid_destination_without_changing_sources(locations, monkeypatch, where):
    data, progress, target = locations
    if where == "space":
        monkeypatch.setattr(migration.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})())
    if where == "occupied":
        target.mkdir(parents=True)
        (target / "foreign").write_bytes(b"untouched")
    destination = {
        "inside_data": data / "TransBridge",
        "inside_progress": progress / "TransBridge",
        "parent": data.parent,
        "fixed": progress.parent,
    }.get(where, target)
    with pytest.raises((ValueError, OSError)):
        migration.migrate_storage(destination)
    assert active_storage_root() is None
    assert (data / "tasks.sqlite3").is_file()
    assert (progress / "run-123" / "进度.json").is_file()
    if where == "occupied":
        assert (target / "foreign").read_bytes() == b"untouched"


@pytest.mark.parametrize("failure", ["copy", "verify", "changed", "switch"])
def test_failure_restores_empty_destination_and_source(locations, monkeypatch, failure):
    data, progress, target = locations
    target.mkdir(parents=True)

    def fail(*_args, **_kwargs):
        raise OSError("injected failure")

    if failure == "copy":
        monkeypatch.setattr(migration.shutil, "copyfileobj", fail)
    elif failure == "verify":
        monkeypatch.setattr(migration, "_identical", lambda *_args: False)
    elif failure == "switch":
        monkeypatch.setattr(migration, "commit_storage_root", fail)

    def add_file(_message):
        if failure == "changed":
            (data / "late-change.txt").write_text("new")

    with pytest.raises(OSError):
        migration.migrate_storage(target, add_file)
    assert list(target.iterdir()) == []
    assert list(target.parent.iterdir()) == [target]
    assert active_storage_root() is None
    assert (data / "tasks.sqlite3").is_file()
    assert (progress / "run-123" / "进度.json").is_file()


def test_development_checkout_data_is_preserved(locations, monkeypatch):
    data, progress, target = locations
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(migration, "default_data_path", lambda: data)
    assert migration.migrate_storage(target) == ()
    assert active_storage_root() == target
    assert (data / "settings.ini").is_file()
    assert not progress.exists()


def test_failed_switch_keeps_newly_added_destination_files(locations, monkeypatch):
    data, _, target = locations

    def failed_switch(_):
        (target / "data" / "unrelated.txt").write_text("keep")
        raise OSError("cannot save location")

    monkeypatch.setattr(migration, "commit_storage_root", failed_switch)
    with pytest.raises(OSError, match="cannot save location"):
        migration.migrate_storage(target)
    assert (target / "data" / "unrelated.txt").read_text() == "keep"
    assert active_storage_root() is None
    assert (data / "settings.ini").is_file()


@pytest.mark.parametrize("name", ["task-store-v1/tasks.sqlite3", "project-store-v1/id-demo/project.sqlite3"])
def test_corrupt_managed_database_blocks_switch(locations, name):
    data, _, target = locations
    database = data / name
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a database")
    with pytest.raises(OSError, match="数据库检查失败"):
        migration.migrate_storage(target)
    assert active_storage_root() is None
    assert not target.exists()
    assert database.read_bytes() == b"not a database"


def test_validation_of_wal_never_changes_published_files(locations, tmp_path):
    data, _, target = locations
    snapshot = data / "task-store-v1"
    snapshot.mkdir()
    writer = sqlite3.connect(tmp_path / "tasks.sqlite3")
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("CREATE TABLE progress (value)")
        writer.execute("INSERT INTO progress VALUES (42)")
        writer.commit()
        for suffix in ("", "-wal"):
            name = "tasks.sqlite3" + suffix
            shutil.copyfile(tmp_path / name, snapshot / name)
    finally:
        writer.close()
    original = {p.name: p.read_bytes() for p in snapshot.iterdir()}

    assert migration.migrate_storage(target) == ()
    published = target / "data" / "task-store-v1"
    assert {p.name: p.read_bytes() for p in published.iterdir()} == original
    db = sqlite3.connect(published / "tasks.sqlite3")
    try:
        assert db.execute("SELECT value FROM progress").fetchone() == (42,)
    finally:
        db.close()


def test_migration_refuses_redirected_source_directory(locations):
    data, _, target = locations
    outside = data.parent / "external"
    outside.mkdir()
    unrelated = outside / "user.txt"
    unrelated.write_text("keep")
    link = data / "linked"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="链接或目录联接"):
        migration.migrate_storage(target)
    assert active_storage_root() is None
    assert unrelated.read_text() == "keep"
    assert not target.exists()


def test_concurrent_migration_is_refused(locations):
    _, _, target = locations
    from transbridge.config.storage_access import storage_access

    with storage_access(exclusive=True), pytest.raises((RuntimeError, OSError)):
        migration.migrate_storage(target)
    assert active_storage_root() is None
    assert not target.exists()
