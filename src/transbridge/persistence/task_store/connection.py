# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Lazy, bounded, rollback-journal transactions for task storage."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .schema import APPLICATION_ID, SCHEMA, VERSION


class TaskStoreError(ValueError):
    """A task database operation could not complete."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@contextmanager
def transaction(path: Path, *, write=False):
    if not write and not path.exists():
        yield None
        return
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    mode = "rwc" if write else "ro"
    connection = None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=2, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        if write:
            connection.execute("PRAGMA synchronous = FULL")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise TaskStoreError("Task database requires DELETE journal mode")
        if (
            (application_id, version) == (0, 0)
            and write
            and not connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
        ):
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {VERSION}")
        elif (application_id, version) != (APPLICATION_ID, VERSION):
            raise TaskStoreError("Unsupported task database format")
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        raise TaskStoreError(f"Task database transaction failed: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
