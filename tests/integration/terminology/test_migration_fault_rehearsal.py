from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from transbridge.application.terminology.models import ArtifactKind, ArtifactStatus
from transbridge.application.terminology.renderers._ledger import (
    ArtifactRenderCoordinator,
    ArtifactRenderError,
    pending_artifact,
)
from transbridge.persistence.terminology import (
    SqliteTerminologyRepository,
    StorageMode,
    TerminologyConnectionFactory,
    TerminologyPaths,
    TerminologyStorageError,
    TerminologyStorageReadOnlyError,
    migration as terminology_migration,
)
from transbridge.persistence.terminology.migration import TerminologyMigrationError, TerminologyMigrator
from transbridge.persistence.terminology.schema import SCHEMA_VERSION
from transbridge.persistence.v2 import LoadedRecord, ProjectId, ProjectRef, ProjectRepository
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.models import AtomicWriteError

pytestmark = pytest.mark.integration


def _project_v2_bytes() -> bytes:
    return json.dumps(
        {
            "schema_version": 2,
            "entity_type": "project",
            "id": "project-1",
            "revision": 4,
            "data": {
                "name": "Project",
                "sources": [
                    {
                        "source_id": "f" * 64,
                        "format_id": "plugin.sse",
                        "location": "C:/mods/base.esp",
                        "fingerprint": "f" * 64,
                        "role": "primary",
                    },
                    {
                        "source_id": "legacy:xml",
                        "format_id": "xml.eet",
                        "location": "C:/mods/base.xml",
                        "fingerprint": "e" * 64,
                        "role": "migration",
                    },
                ],
                "variant_ids": ["main"],
                "active_variant_id": "main",
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def test_project_v3_migration_uses_a_copy_and_retains_verified_v2_backup(tmp_path: Path) -> None:
    fixture = tmp_path / "project-v2-copy.json"
    fixture.write_bytes(_project_v2_bytes())
    root = tmp_path / "repository"
    repository = ProjectRepository(str(root.resolve()), OsPersistenceFilesystem())
    ref = ProjectRef(ProjectId("project-1"))
    destination = Path(repository.path_for(ref))
    destination.parent.mkdir(parents=True)
    shutil.copy2(fixture, destination)

    result = repository.load(ref)

    assert isinstance(result, LoadedRecord) and result.migrated
    assert result.value.envelope.schema_version == 3
    assert result.migration_report is not None
    assert Path(result.migration_report.backup_path).read_bytes() == fixture.read_bytes()
    assert json.loads(destination.read_bytes())["schema_version"] == 3


class _CrashOnProjectReplace(OsPersistenceFilesystem):
    def __init__(self, record: Path) -> None:
        self._record = record.resolve()

    def replace(self, source: str, destination: str) -> None:
        if Path(destination).resolve() == self._record:
            raise OSError("injected crash before authoritative replace")
        super().replace(source, destination)


def test_project_migration_crash_preserves_authoritative_v2_and_backup(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initial = ProjectRepository(str(root.resolve()), OsPersistenceFilesystem())
    ref = ProjectRef(ProjectId("project-1"))
    destination = Path(initial.path_for(ref))
    destination.parent.mkdir(parents=True)
    original = _project_v2_bytes()
    destination.write_bytes(original)
    crashing = ProjectRepository(str(root.resolve()), _CrashOnProjectReplace(destination))

    with pytest.raises(AtomicWriteError):
        crashing.load(ref)

    assert destination.read_bytes() == original
    backups = tuple((root / "backups").rglob("*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_sqlite_copy_migration_is_backup_first_and_forces_no_wal(tmp_path: Path) -> None:
    fixture = tmp_path / "terminology-v0.sqlite3"
    legacy = sqlite3.connect(fixture)
    legacy.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
    legacy.execute("INSERT INTO legacy_sentinel(value) VALUES ('preserved')")
    legacy.commit()
    legacy.close()
    paths = TerminologyPaths(tmp_path / "assets")
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)
    shutil.copy2(fixture, database)

    opened = TerminologyConnectionFactory(paths, allow_wal=False).open("project-1")
    try:
        assert opened.state.journal_mode == "delete"
        assert opened.connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "preserved"
        recorded_backup = Path(opened.connection.execute("SELECT backup_path FROM migration_history").fetchone()[0])
    finally:
        opened.connection.close()
    assert recorded_backup.parent == paths.backup_directory("project-1")
    assert recorded_backup.name.startswith(f"schema-v0-to-v{SCHEMA_VERSION}-")
    assert recorded_backup.is_file()
    check = sqlite3.connect(recorded_backup)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "preserved"
    finally:
        check.close()

    expected_digest = hashlib.sha256(recorded_backup.read_bytes()).hexdigest()
    evidence = sqlite3.connect(database)
    try:
        row = evidence.execute(
            "SELECT source_digest, backup_digest, backup_path FROM migration_history_evidence"
        ).fetchone()
    finally:
        evidence.close()
    assert row == (expected_digest, expected_digest, str(recorded_backup))


def test_sqlite_migration_retry_after_source_change_creates_a_new_digest_bound_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = TerminologyPaths(tmp_path / "assets")
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)

    first = sqlite3.connect(database)
    first.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
    first.execute("INSERT INTO legacy_sentinel(value) VALUES ('first')")
    first.commit()
    first.close()
    connection = sqlite3.connect(database, isolation_level=None)
    original_initialize = terminology_migration.initialize_schema

    def fail_after_backup(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(terminology_migration, "initialize_schema", fail_after_backup)
    with pytest.raises(TerminologyMigrationError, match="migration failed"):
        TerminologyMigrator(paths).migrate(connection, "project-1", 0)
    connection.close()
    first_backup = next(paths.backup_directory("project-1").glob("schema-v0-to-v4-*.sqlite3"))

    changed = sqlite3.connect(database)
    changed.execute("UPDATE legacy_sentinel SET value = 'changed-after-failure'")
    changed.commit()
    changed.close()
    monkeypatch.setattr(terminology_migration, "initialize_schema", original_initialize)

    second_opened = TerminologyConnectionFactory(paths).open("project-1")
    try:
        second_backup = Path(
            second_opened.connection.execute("SELECT backup_path FROM migration_history").fetchone()[0]
        )
    finally:
        second_opened.connection.close()

    assert second_backup != first_backup
    assert first_backup.is_file() and second_backup.is_file()
    first_copy = sqlite3.connect(first_backup)
    second_copy = sqlite3.connect(second_backup)
    try:
        assert first_copy.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "first"
        assert second_copy.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "changed-after-failure"
    finally:
        first_copy.close()
        second_copy.close()


def test_future_and_corrupt_sqlite_copies_never_become_empty_writable_databases(tmp_path: Path) -> None:
    future_paths = TerminologyPaths(tmp_path / "future")
    future_database = future_paths.database("project-1")
    future_database.parent.mkdir(parents=True)
    future = sqlite3.connect(future_database)
    future.execute("PRAGMA user_version = 999")
    future.commit()
    future.close()
    future_bytes = future_database.read_bytes()

    repository = SqliteTerminologyRepository(TerminologyConnectionFactory(future_paths), "project-1")
    try:
        assert repository.storage_state.mode is StorageMode.READ_ONLY
        with pytest.raises(TerminologyStorageReadOnlyError):
            repository.put_artifact(
                pending_artifact(
                    owner_ref="version-1",
                    owner_digest="digest-1",
                    kind=ArtifactKind.CHANGELOG_MARKDOWN,
                    renderer_version="1",
                    target="changelog.md",
                )
            )
    finally:
        repository.close()
    assert future_database.read_bytes() == future_bytes

    corrupt_paths = TerminologyPaths(tmp_path / "corrupt")
    corrupt_database = corrupt_paths.database("project-1")
    corrupt_database.parent.mkdir(parents=True)
    corrupt_bytes = b"corrupt terminology database; retain for recovery"
    corrupt_database.write_bytes(corrupt_bytes)
    with pytest.raises(TerminologyStorageError):
        TerminologyConnectionFactory(corrupt_paths).open("project-1")
    assert corrupt_database.read_bytes() == corrupt_bytes


def test_sqlite_crash_rolls_back_staged_facts_and_artifact_retry_preserves_owner(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        connection = repository._connection
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO artifact_ledger(artifact_id, owner_ref, kind, renderer_version, content_digest, target, "
            "status, retry_count, diagnostic, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("crash-staged", "version-1", "changelog_markdown", "1", "digest", "target", "pending", 0, None, "{}"),
        )
        connection.rollback()
        assert (
            connection.execute("SELECT count(*) FROM artifact_ledger WHERE artifact_id='crash-staged'").fetchone()[0]
            == 0
        )

        pending = pending_artifact(
            owner_ref="version-1",
            owner_digest="digest-1",
            kind=ArtifactKind.CHANGELOG_MARKDOWN,
            renderer_version="1",
            target=str(tmp_path / "changelog.md"),
        )
        coordinator = ArtifactRenderCoordinator(repository)
        with pytest.raises(ArtifactRenderError):
            coordinator.render(pending, lambda: (_ for _ in ()).throw(OSError("injected renderer failure")))
        failed = repository.get_artifact(pending.artifact_id)
        assert failed is not None and failed.status is ArtifactStatus.FAILED

        rendered = SimpleNamespace(path=tmp_path / "changelog.md")
        _, succeeded = coordinator.render(pending, lambda: rendered)
        assert succeeded.status is ArtifactStatus.SUCCEEDED
        assert succeeded.retry_count == 1
        assert (succeeded.owner_ref, succeeded.content_digest) == ("version-1", "digest-1")
    finally:
        repository.close()
