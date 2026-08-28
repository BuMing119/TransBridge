"""Backup-first migration and integrity verification for terminology SQLite."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from transbridge.application.terminology.models import ChangeLogDocument, TerminologyReportSnapshot

from .changelog import ChangelogDocumentStore
from .codec import loads
from .paths import TerminologyPaths
from .report_snapshot import SqliteReportSnapshotStore
from .schema import SCHEMA_VERSION, initialize_schema


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    project_id: str
    from_version: int
    to_version: int
    backup_path: Path
    integrity_result: str
    source_digest: str = ""
    backup_digest: str = ""


class TerminologyMigrationError(RuntimeError):
    pass


class TerminologyMigrator:
    def __init__(self, paths: TerminologyPaths) -> None:
        self._paths = paths

    def migrate(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        from_version: int,
    ) -> MigrationManifest:
        if from_version < 0 or from_version >= SCHEMA_VERSION:
            raise TerminologyMigrationError(f"no migration path from terminology schema {from_version}")
        integrity = integrity_check(connection)
        if integrity != "ok":
            raise TerminologyMigrationError(f"source database integrity check failed: {integrity}")

        backup_path, source_digest, backup_digest = self._create_digest_bound_backup(
            connection,
            project_id,
            from_version,
        )

        try:
            connection.execute("BEGIN IMMEDIATE")
            if from_version == 1:
                connection.execute(
                    "ALTER TABLE artifact_ledger ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)"
                )
                _upgrade_artifact_payloads(connection)
            initialize_schema(connection)
            _backfill_frozen_sections(connection)
            _ensure_migration_evidence_table(connection)
            connection.execute(
                "INSERT INTO migration_history(from_version, to_version, backup_path) VALUES (?, ?, ?)",
                (from_version, SCHEMA_VERSION, str(backup_path)),
            )
            connection.execute(
                "INSERT INTO migration_history_evidence("
                "from_version, to_version, source_digest, backup_digest, backup_path"
                ") VALUES (?, ?, ?, ?, ?)",
                (from_version, SCHEMA_VERSION, source_digest, backup_digest, str(backup_path)),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, TerminologyMigrationError):
                raise
            raise TerminologyMigrationError("terminology schema migration failed") from exc
        if integrity_check(connection) != "ok":
            raise TerminologyMigrationError("migrated database failed integrity verification")
        return MigrationManifest(
            project_id=project_id,
            from_version=from_version,
            to_version=SCHEMA_VERSION,
            backup_path=backup_path,
            integrity_result="ok",
            source_digest=source_digest,
            backup_digest=backup_digest,
        )

    def _create_digest_bound_backup(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        from_version: int,
    ) -> tuple[Path, str, str]:
        staging_path = self._paths.staging(
            project_id,
            f"migration-backup-{from_version}-{SCHEMA_VERSION}-{uuid.uuid4().hex}",
        )
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(staging_path)
        try:
            connection.backup(backup)
            backup.commit()
        except sqlite3.Error as exc:
            backup.close()
            staging_path.unlink(missing_ok=True)
            raise TerminologyMigrationError("failed to create consistent SQLite migration backup") from exc
        else:
            backup.close()

        try:
            if integrity_check_path(staging_path) != "ok":
                raise TerminologyMigrationError("new migration backup failed integrity verification")
            source_digest = _file_sha256(staging_path)
            backup_path = self._paths.backup(
                project_id,
                from_version,
                SCHEMA_VERSION,
                source_digest=source_digest,
            )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if backup_path.exists():
                backup_digest = _verified_backup_digest(backup_path, existing=True)
                if backup_digest != source_digest:
                    raise TerminologyMigrationError("existing migration backup does not match the current source")
                staging_path.unlink(missing_ok=True)
            else:
                try:
                    staging_path.replace(backup_path)
                except OSError as exc:
                    raise TerminologyMigrationError("failed to publish SQLite migration backup") from exc
                backup_digest = _verified_backup_digest(backup_path, existing=False)
                if backup_digest != source_digest:
                    backup_path.unlink(missing_ok=True)
                    raise TerminologyMigrationError("new migration backup does not match the captured source")
            return backup_path, source_digest, backup_digest
        except Exception:
            staging_path.unlink(missing_ok=True)
            raise


def integrity_check(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise TerminologyMigrationError("SQLite integrity check could not run") from exc
    result = "missing-result" if row is None else str(row[0])
    if result != "ok":
        return result
    foreign_key_row = connection.execute("PRAGMA foreign_key_check").fetchone()
    return "ok" if foreign_key_row is None else f"foreign-key:{tuple(foreign_key_row)}"


def integrity_check_path(path: Path) -> str:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        return integrity_check(connection)
    finally:
        connection.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_backup_digest(path: Path, *, existing: bool) -> str:
    integrity = integrity_check_path(path)
    if integrity != "ok":
        label = "existing" if existing else "new"
        if not existing:
            path.unlink(missing_ok=True)
        raise TerminologyMigrationError(f"{label} migration backup failed integrity verification")
    return _file_sha256(path)


def _ensure_migration_evidence_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS migration_history_evidence ("
        "from_version INTEGER NOT NULL, "
        "to_version INTEGER NOT NULL, "
        "source_digest TEXT NOT NULL CHECK(length(source_digest) = 64), "
        "backup_digest TEXT NOT NULL CHECK(length(backup_digest) = 64), "
        "backup_path TEXT NOT NULL, "
        "completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (from_version, to_version)"
        ")"
    )


def _upgrade_artifact_payloads(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT artifact_id, payload_json FROM artifact_ledger").fetchall()
    for artifact_id, payload_json in rows:
        payload = json.loads(str(payload_json))
        fields = payload.get("fields") if isinstance(payload, dict) else None
        if not isinstance(fields, dict):
            raise TerminologyMigrationError("artifact ledger payload is not canonical")
        fields.setdefault("revision", 0)
        connection.execute(
            "UPDATE artifact_ledger SET revision = 0, payload_json = ? WHERE artifact_id = ?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), str(artifact_id)),
        )


def _backfill_frozen_sections(connection: sqlite3.Connection) -> None:
    reports = SqliteReportSnapshotStore(connection)
    for (payload_json,) in connection.execute("SELECT payload_json FROM report_snapshots").fetchall():
        reports.put_report_snapshot(loads(str(payload_json), TerminologyReportSnapshot))
    changelogs = ChangelogDocumentStore(connection)
    rows = connection.execute("SELECT version_key, payload_json FROM changelog_documents").fetchall()
    for version_key, payload_json in rows:
        changelogs.put(loads(str(payload_json), ChangeLogDocument), version_key=str(version_key))


__all__ = [
    "MigrationManifest",
    "TerminologyMigrationError",
    "TerminologyMigrator",
    "integrity_check",
    "integrity_check_path",
]
