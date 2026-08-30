from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from tests.application.terminology.story08_support import decision, draft
from tests.contracts.terminology.test_repository_contract import _build, _version
from transbridge.application.terminology.diff import CanonicalDiffEngine
from transbridge.application.terminology.models import ArtifactKind, ArtifactLedgerEntry
from transbridge.application.terminology.narrative import ChangeNarrativeProjector
from transbridge.application.terminology.ports import SnapshotCursor
from transbridge.application.terminology.reports import TerminologyReportSnapshotFactory
from transbridge.persistence.terminology import (
    CursorCodec,
    SqliteTerminologyRepository,
    StorageMode,
    TerminologyConnectionFactory,
    TerminologyPaths,
    TerminologyStorageError,
    TerminologyStorageFullError,
    TerminologyStorageReadOnlyError,
)
from transbridge.persistence.terminology.connection import translate_sqlite_error
from transbridge.persistence.terminology.schema import SCHEMA_VERSION
from transbridge.persistence.v2.models import PathBoundaryError


def test_paths_are_project_isolated_and_root_guarded(tmp_path: Path) -> None:
    paths = TerminologyPaths(tmp_path.resolve())

    assert paths.database("project-1") != paths.database("project-2")
    assert paths.database("project-1").is_relative_to(tmp_path)
    with pytest.raises(PathBoundaryError):
        paths.guard(tmp_path.parent / "outside.sqlite3")
    with pytest.raises(ValueError):
        paths.database("../escape")


def test_connection_enables_foreign_keys_timeout_and_conservative_journal(tmp_path: Path) -> None:
    factory = TerminologyConnectionFactory(TerminologyPaths(tmp_path.resolve()), busy_timeout_ms=3210)
    opened = factory.open("project-1")
    try:
        assert opened.state.mode is StorageMode.CREATE
        assert opened.state.journal_mode == "delete"
        assert opened.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert opened.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 3210
        assert opened.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        opened.connection.close()


def test_wal_is_only_enabled_by_explicit_local_capability(tmp_path: Path) -> None:
    factory = TerminologyConnectionFactory(TerminologyPaths(tmp_path.resolve()), allow_wal=True)
    opened = factory.open("project-1")
    try:
        assert opened.state.journal_mode == "wal"
    finally:
        opened.connection.close()


def test_existing_schema_zero_is_backed_up_before_migration(tmp_path: Path) -> None:
    paths = TerminologyPaths(tmp_path.resolve())
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)
    legacy = sqlite3.connect(database)
    legacy.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
    legacy.execute("INSERT INTO legacy_sentinel(value) VALUES ('preserved')")
    legacy.commit()
    legacy.close()

    opened = TerminologyConnectionFactory(paths).open("project-1")
    try:
        assert opened.state.schema_version == 3
        assert opened.connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "preserved"
    finally:
        opened.connection.close()
    backup = next(paths.backup_directory("project-1").glob("schema-v0-to-v3-*.sqlite3"))
    backup_connection = sqlite3.connect(backup)
    try:
        assert backup_connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "preserved"
        assert backup_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        backup_connection.close()

    history = TerminologyConnectionFactory(paths).open("project-1", writable=False)
    try:
        evidence = history.connection.execute(
            "SELECT source_digest, backup_digest, backup_path FROM migration_history_evidence "
            "WHERE from_version = 0 AND to_version = 3"
        ).fetchone()
    finally:
        history.connection.close()
    assert evidence is not None
    expected_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    assert tuple(evidence) == (expected_digest, expected_digest, str(backup))


def test_schema_one_migration_backfills_frozen_sections_and_artifact_revision(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    result = _build()
    repository.put_build(result)
    snapshot = TerminologyReportSnapshotFactory(repository).freeze(result.ref, draft=draft())
    repository.put_report_snapshot(snapshot)
    version = _version(result, "version-1")
    repository.publish_version(version, expected_effective_version_id=None)
    diff = CanonicalDiffEngine().compare(None, target_version_id="version-1", decisions=(decision(),))
    document = ChangeNarrativeProjector().project(
        version_ref=version.ref,
        diff=diff,
        decisions=(decision(),),
        conflicts=(),
        manual_actions=(),
    )
    repository.put_changelog(document)
    artifact = ArtifactLedgerEntry(
        "artifact-v1",
        document.ref.document_id,
        ArtifactKind.CHANGELOG_MARKDOWN,
        "renderer-v1",
        document.ref.content_digest,
        "changes.md",
    )
    repository.put_artifact(artifact)
    database = repository.path
    repository.close()

    legacy = sqlite3.connect(database)
    try:
        payload = json.loads(legacy.execute("SELECT payload_json FROM artifact_ledger").fetchone()[0])
        del payload["fields"]["revision"]
        legacy.execute(
            "UPDATE artifact_ledger SET payload_json = ?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),),
        )
        legacy.execute("DROP TABLE report_snapshot_sections")
        legacy.execute("DROP TABLE report_snapshot_manifests")
        legacy.execute("DROP TABLE changelog_sections")
        legacy.execute("DROP TABLE changelog_manifests")
        legacy.execute("ALTER TABLE artifact_ledger DROP COLUMN revision")
        legacy.execute("UPDATE schema_metadata SET schema_version = 1 WHERE singleton = 1")
        legacy.execute("PRAGMA user_version = 1")
        legacy.commit()
    finally:
        legacy.close()

    migrated = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        assert migrated.storage_state.schema_version == 3
        assert migrated.list_report_terms(snapshot.ref).items == snapshot.terms
        assert migrated.changelogs.list_changelog_changes(document.ref).items == document.changes
        assert migrated.get_artifact(artifact.artifact_id).revision == 0
        assert tuple(TerminologyPaths(tmp_path).backup_directory("project-1").glob("schema-v1-to-v3-*.sqlite3"))
    finally:
        migrated.close()


def test_future_schema_opens_read_only_and_refuses_writes(tmp_path: Path) -> None:
    paths = TerminologyPaths(tmp_path.resolve())
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)
    future = sqlite3.connect(database)
    future.execute("PRAGMA user_version = 999")
    future.commit()
    future.close()

    repository = SqliteTerminologyRepository(TerminologyConnectionFactory(paths), "project-1")
    try:
        assert repository.storage_state.mode is StorageMode.READ_ONLY
        assert repository.storage_state.schema_version == 999
        with pytest.raises(TerminologyStorageReadOnlyError):
            repository.put_build(_build())
    finally:
        repository.close()


def test_corrupt_database_is_not_replaced_with_empty_storage(tmp_path: Path) -> None:
    paths = TerminologyPaths(tmp_path.resolve())
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)
    original = b"not a sqlite database"
    database.write_bytes(original)

    with pytest.raises(TerminologyStorageError) as exc_info:
        TerminologyConnectionFactory(paths).open("project-1")

    assert exc_info.value.state.mode is StorageMode.READ_ONLY
    assert not exc_info.value.state.integrity_ok
    assert database.read_bytes() == original


def test_current_but_incomplete_schema_is_rejected_without_initialization(tmp_path: Path) -> None:
    paths = TerminologyPaths(tmp_path.resolve())
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)
    incomplete = sqlite3.connect(database)
    incomplete.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    incomplete.commit()
    incomplete.close()

    with pytest.raises(TerminologyStorageError) as exc_info:
        TerminologyConnectionFactory(paths).open("project-1")

    assert exc_info.value.state.mode is StorageMode.READ_ONLY
    check = sqlite3.connect(database)
    try:
        assert check.execute("SELECT name FROM sqlite_master WHERE name = 'builds'").fetchone() is None
    finally:
        check.close()


def test_sqlite_full_is_translated_to_safe_storage_failure(tmp_path: Path) -> None:
    state = TerminologyConnectionFactory(TerminologyPaths(tmp_path.resolve())).open("project-1")
    try:
        translated = translate_sqlite_error(sqlite3.OperationalError("database or disk is full"), state.state)
        assert isinstance(translated, TerminologyStorageFullError)
    finally:
        state.connection.close()


def test_cursor_codec_pins_schema_snapshot_query_sort_and_stable_id() -> None:
    cursor = SnapshotCursor("snapshot-1", "query-1", ("name", "term-1"), "term-1")

    decoded = CursorCodec.decode(CursorCodec.encode(cursor))

    assert decoded == cursor
