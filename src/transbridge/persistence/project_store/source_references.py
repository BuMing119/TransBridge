# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Optional file references attached to stored source revisions."""

from __future__ import annotations

import sqlite3

from transbridge.application.projects.storage_contracts import validate_source_reference_path

from .entry_codec import pack_text, unpack_text

SCHEMA = """CREATE TABLE source_reference (
    source_id INTEGER PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
    path TEXT NOT NULL CHECK (path <> '')
) STRICT"""


def installed(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'source_reference'").fetchone()
        is not None
    )


def read_reference_path(connection: sqlite3.Connection, source_id: int) -> str:
    if not installed(connection):
        return ""
    row = connection.execute("SELECT path FROM source_reference WHERE source_id = ?", (source_id,)).fetchone()
    return "" if row is None else unpack_text(row["path"])


def write_reference_path(connection: sqlite3.Connection, source_id: int, path: str) -> None:
    validate_source_reference_path(path)
    if not installed(connection):
        if not path:
            return
        connection.execute(SCHEMA)
    if path:
        connection.execute(
            """
            INSERT INTO source_reference (source_id, path) VALUES (?, ?)
            ON CONFLICT (source_id) DO UPDATE SET path = excluded.path
            """,
            (source_id, pack_text(path)),
        )
    else:
        connection.execute("DELETE FROM source_reference WHERE source_id = ?", (source_id,))


def validate_references(connection: sqlite3.Connection) -> None:
    if installed(connection):
        for (value,) in connection.execute("SELECT path FROM source_reference"):
            path = unpack_text(value)
            validate_source_reference_path(path)
            if not path or pack_text(path) != value:
                raise ValueError("source reference is not canonical text")
