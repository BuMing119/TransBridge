"""Thread-local, bounded SQLite transactions and format admission."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
import sqlite3

from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.session_write_lease import persistence_write_lease

from .format import (
    PROJECT_APPLICATION_ID,
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    PROJECT_ROOT,
    ProjectStoreError,
    UnsupportedProjectFormat,
)
from .schema import SCHEMA


@contextmanager
def project_connection(
    path: Path, *, write: bool = False, create: bool = False, check_schema: bool = True
) -> Iterator[sqlite3.Connection]:
    lease = project_mutation_lease(path) if write else nullcontext()
    with lease, _project_connection(path, write=write, create=create, check_schema=check_schema) as connection:
        yield connection


def project_mutation_lease(path: Path):
    root = path.parent.parent
    if path.name != PROJECT_FILENAME or not path.parent.name.startswith("id-") or root.name != PROJECT_ROOT:
        return nullcontext()
    return persistence_write_lease(str(root), path.parent.name, OsPersistenceFilesystem(), "PROJECT_WRITER_BUSY")


@contextmanager
def _project_connection(path: Path, *, write: bool, create: bool, check_schema: bool) -> Iterator[sqlite3.Connection]:
    if create and not write:
        raise ValueError("database creation requires a write transaction")
    if not create and not path.is_file():
        raise UnsupportedProjectFormat("project database is missing")
    mode = "rwc" if create else "rw" if write else "ro"
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=2, isolation_level=None
        )
    except sqlite3.Error as exc:
        raise ProjectStoreError(f"Cannot open project database: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        if write:
            connection.execute("PRAGMA synchronous = FULL")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        if create:
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {PROJECT_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {PROJECT_FORMAT_VERSION}")
        else:
            _validate_format(connection, check_schema=check_schema)
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        if getattr(exc, "sqlite_errorcode", 0) & 0xFF in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            raise UnsupportedProjectFormat("damaged or unrecognized project database") from exc
        raise ProjectStoreError(f"Project database transaction failed: {exc}") from exc
    finally:
        connection.close()


def _validate_format(connection: sqlite3.Connection, *, check_schema: bool) -> None:
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != PROJECT_APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != PROJECT_FORMAT_VERSION
    ):
        raise UnsupportedProjectFormat("database application ID or version differs")
    if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        raise UnsupportedProjectFormat("project database requires DELETE journal mode")
    if check_schema:
        actual = {row[0] for row in connection.execute("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL")}
        if actual != set(SCHEMA):
            raise UnsupportedProjectFormat("database schema differs from the supported project schema")
    try:
        project = connection.execute("SELECT format, deleted FROM project WHERE singleton = 1").fetchone()
    except sqlite3.OperationalError as exc:
        raise UnsupportedProjectFormat("project metadata table is missing or invalid") from exc
    if project is None or project["format"] != PROJECT_FORMAT:
        raise UnsupportedProjectFormat("project metadata is missing or invalid")
    if project["deleted"]:
        raise ProjectStoreError("project has been deleted")
