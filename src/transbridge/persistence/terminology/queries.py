"""Snapshot-bound cursor codec and SQLite keyset pagination helpers."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any

from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor

CURSOR_SCHEMA_VERSION = 1


class CursorCodec:
    @staticmethod
    def encode(cursor: SnapshotCursor) -> str:
        payload = {
            "schema": CURSOR_SCHEMA_VERSION,
            "snapshot_digest": cursor.snapshot_digest,
            "query_fingerprint": cursor.query_fingerprint,
            "sort_values": cursor.sort_values,
            "last_stable_id": cursor.stable_id,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode(token: str) -> SnapshotCursor:
        try:
            padding = "=" * (-len(token) % 4)
            payload = json.loads(base64.urlsafe_b64decode(token + padding))
            if payload["schema"] != CURSOR_SCHEMA_VERSION:
                raise ValueError("unsupported cursor schema")
            return SnapshotCursor(
                snapshot_digest=str(payload["snapshot_digest"]),
                query_fingerprint=str(payload["query_fingerprint"]),
                sort_values=tuple(str(item) for item in payload["sort_values"]),
                stable_id=str(payload["last_stable_id"]),
            )
        except Exception as exc:
            if isinstance(exc, ValueError) and str(exc) == "unsupported cursor schema":
                raise
            raise ValueError("invalid terminology cursor token") from exc


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    value: str

    @classmethod
    def from_fields(cls, **fields: Any) -> QueryFingerprint:
        raw = json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return cls(hashlib.sha256(raw).hexdigest())


def keyset_page[T](
    connection: sqlite3.Connection,
    *,
    table: str,
    owner_column: str,
    owner_value: str,
    snapshot_digest: str,
    request: PageRequest,
    decode: Callable[[str], T],
) -> Page[T]:
    cursor = request.cursor
    if cursor is not None and (
        cursor.snapshot_digest != snapshot_digest or cursor.query_fingerprint != request.query_fingerprint
    ):
        raise CursorStaleError("cursor does not belong to this snapshot and query")
    if cursor is not None and cursor.sort_values != (cursor.stable_id,):
        raise CursorStaleError("cursor sort key does not match the stable keyset order")
    after_id = None if cursor is None else cursor.stable_id
    where = f"{owner_column} = ?"
    parameters: list[Any] = [owner_value]
    if after_id is not None:
        where += " AND stable_id > ?"
        parameters.append(after_id)
    rows = connection.execute(
        f"SELECT stable_id, payload_json FROM {table} WHERE {where} ORDER BY stable_id LIMIT ?",
        (*parameters, request.limit + 1),
    ).fetchall()
    total = int(
        connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {owner_column} = ?", (owner_value,)).fetchone()[0]
    )
    visible = rows[: request.limit]
    next_cursor = None
    if len(rows) > request.limit and visible:
        stable_id = str(visible[-1]["stable_id"])
        next_cursor = SnapshotCursor(snapshot_digest, request.query_fingerprint, (stable_id,), stable_id)
    return Page(tuple(decode(str(row["payload_json"])) for row in visible), snapshot_digest, next_cursor, total)


__all__ = ["CURSOR_SCHEMA_VERSION", "CursorCodec", "QueryFingerprint", "keyset_page"]
