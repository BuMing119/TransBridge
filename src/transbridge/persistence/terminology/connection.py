"""Connection policy and safe storage-state detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import sqlite3

from .migration import TerminologyMigrationError, TerminologyMigrator, integrity_check
from .paths import TerminologyPaths
from .schema import SCHEMA_VERSION, initialize_schema, validate_schema


class StorageMode(StrEnum):
    CREATE = "create"
    READ_WRITE = "read-write"
    READ_ONLY = "read-only"


@dataclass(frozen=True, slots=True)
class TerminologyStorageState:
    mode: StorageMode
    schema_version: int | None
    journal_mode: str | None
    integrity_ok: bool
    diagnostic: str | None = None


class TerminologyStorageError(RuntimeError):
    def __init__(self, message: str, state: TerminologyStorageState) -> None:
        super().__init__(message)
        self.state = state


class TerminologyStorageReadOnlyError(TerminologyStorageError):
    pass


class TerminologyStorageFullError(TerminologyStorageError):
    pass


@dataclass(slots=True)
class OpenedTerminologyConnection:
    connection: sqlite3.Connection
    state: TerminologyStorageState
    path: Path


class TerminologyConnectionFactory:
    def __init__(
        self,
        paths: TerminologyPaths,
        *,
        allow_wal: bool = False,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy timeout must be positive")
        self.paths = paths
        self.allow_wal = allow_wal
        self.busy_timeout_ms = busy_timeout_ms
        self._migrator = TerminologyMigrator(paths)

    def open(self, project_id: str, *, writable: bool = True) -> OpenedTerminologyConnection:
        path = self.paths.database(project_id)
        existed = path.exists()
        if not existed and not writable:
            state = TerminologyStorageState(StorageMode.READ_ONLY, None, None, False, "database does not exist")
            raise TerminologyStorageReadOnlyError("terminology database does not exist", state)
        if not existed:
            path.parent.mkdir(parents=True, exist_ok=True)

        probe: sqlite3.Connection | None = None
        try:
            version = 0
            if existed:
                probe = self._connect(path, read_only=True)
                self._configure(probe, writable=False)
                version = int(probe.execute("PRAGMA user_version").fetchone()[0])
                probe_integrity = integrity_check(probe)
                if probe_integrity != "ok":
                    probe.close()
                    state = TerminologyStorageState(StorageMode.READ_ONLY, version, None, False, probe_integrity)
                    raise TerminologyStorageError("terminology database is corrupt", state)
                if version > SCHEMA_VERSION:
                    return self._opened_read_only(
                        probe,
                        path,
                        version,
                        f"future schema {version}; supported {SCHEMA_VERSION}",
                    )
                if not writable:
                    diagnostic = "migration required" if version < SCHEMA_VERSION else validate_schema(probe)
                    return self._opened_read_only(probe, path, version, diagnostic)
                if version == SCHEMA_VERSION:
                    schema_diagnostic = validate_schema(probe)
                    if schema_diagnostic is not None:
                        probe.close()
                        probe = None
                        state = TerminologyStorageState(
                            StorageMode.READ_ONLY,
                            version,
                            None,
                            False,
                            schema_diagnostic,
                        )
                        raise TerminologyStorageError("terminology database schema is incomplete", state)
                probe.close()
                probe = None

            connection = self._connect(path, read_only=False)
            self._configure(connection, writable=True)
            if not existed:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    initialize_schema(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                version = SCHEMA_VERSION
                mode = StorageMode.CREATE
            elif version < SCHEMA_VERSION:
                self._migrator.migrate(connection, project_id, version)
                version = SCHEMA_VERSION
                mode = StorageMode.READ_WRITE
            else:
                mode = StorageMode.READ_WRITE
            integrity = integrity_check(connection)
            if integrity != "ok":
                connection.close()
                state = TerminologyStorageState(StorageMode.READ_ONLY, version, None, False, integrity)
                raise TerminologyStorageError("terminology database is corrupt", state)
            schema_diagnostic = validate_schema(connection)
            if schema_diagnostic is not None:
                connection.close()
                state = TerminologyStorageState(StorageMode.READ_ONLY, version, None, False, schema_diagnostic)
                raise TerminologyStorageError("terminology database schema is incomplete", state)
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            state = TerminologyStorageState(mode, version, journal, True)
            return OpenedTerminologyConnection(connection, state, path)
        except TerminologyStorageError:
            raise
        except (sqlite3.Error, TerminologyMigrationError) as exc:
            if probe is not None:
                probe.close()
            state = TerminologyStorageState(StorageMode.READ_ONLY, None, None, False, type(exc).__name__)
            raise TerminologyStorageError("terminology database could not be opened safely", state) from exc

    def _opened_read_only(
        self,
        connection: sqlite3.Connection,
        path: Path,
        version: int,
        diagnostic: str | None = None,
    ) -> OpenedTerminologyConnection:
        journal_row = connection.execute("PRAGMA journal_mode").fetchone()
        journal = None if journal_row is None else str(journal_row[0]).lower()
        state = TerminologyStorageState(StorageMode.READ_ONLY, version, journal, True, diagnostic)
        return OpenedTerminologyConnection(connection, state, path)

    def _configure(self, connection: sqlite3.Connection, *, writable: bool) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        if writable:
            journal = "WAL" if self.allow_wal else "DELETE"
            connection.execute(f"PRAGMA journal_mode = {journal}")
            connection.execute("PRAGMA synchronous = FULL")

    @staticmethod
    def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
        return sqlite3.connect(path, isolation_level=None, check_same_thread=False)


def translate_sqlite_error(exc: sqlite3.Error, state: TerminologyStorageState) -> TerminologyStorageError:
    code = getattr(exc, "sqlite_errorcode", None)
    if code == sqlite3.SQLITE_FULL or "database or disk is full" in str(exc).lower():
        return TerminologyStorageFullError("terminology database is full; write was rolled back", state)
    return TerminologyStorageError("terminology SQLite operation failed", state)


__all__ = [
    "OpenedTerminologyConnection",
    "StorageMode",
    "TerminologyConnectionFactory",
    "TerminologyStorageError",
    "TerminologyStorageFullError",
    "TerminologyStorageReadOnlyError",
    "TerminologyStorageState",
    "translate_sqlite_error",
]
