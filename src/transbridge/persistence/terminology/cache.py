"""Three independently disposable terminology cache layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import sqlite3
import time


class CacheKind(StrEnum):
    BUILD = "build"
    PARSE = "parse"
    EXTRACTION = "extraction"


_TABLES = {
    CacheKind.BUILD: "cache_build",
    CacheKind.PARSE: "cache_parse",
    CacheKind.EXTRACTION: "cache_extraction",
}


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    payload_digest: str
    payload: bytes
    touched_at: int


class TerminologyCache:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def put(self, kind: CacheKind, key: str, payload: bytes, *, payload_digest: str | None = None) -> CacheEntry:
        owns_transaction = not self._connection.in_transaction
        table = _TABLES[CacheKind(kind)]
        digest = payload_digest or hashlib.sha256(payload).hexdigest()
        existing = self._connection.execute(
            f"SELECT payload_digest, payload FROM {table} WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if existing is not None and (
            str(existing["payload_digest"]) != digest or bytes(existing["payload"]) != payload
        ):
            raise ValueError("cache key collision identifies different payload content")
        touched_at = time.time_ns()
        self._connection.execute(
            f"INSERT INTO {table}(cache_key, payload_digest, payload, touched_at, size_bytes) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET touched_at=excluded.touched_at",
            (key, digest, payload, touched_at, len(payload)),
        )
        if owns_transaction:
            self._connection.commit()
        return CacheEntry(key, digest, payload, touched_at)

    def get(self, kind: CacheKind, key: str, *, expected_digest: str | None = None) -> CacheEntry | None:
        owns_transaction = not self._connection.in_transaction
        table = _TABLES[CacheKind(kind)]
        row = self._connection.execute(
            f"SELECT payload_digest, payload, touched_at FROM {table} WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        digest = str(row["payload_digest"])
        payload = bytes(row["payload"])
        if expected_digest is not None and digest != expected_digest:
            return None
        if hashlib.sha256(payload).hexdigest() != digest and expected_digest is None:
            return None
        touched_at = time.time_ns()
        self._connection.execute(f"UPDATE {table} SET touched_at = ? WHERE cache_key = ?", (touched_at, key))
        if owns_transaction:
            self._connection.commit()
        return CacheEntry(key, digest, payload, touched_at)

    def gc(self, kind: CacheKind, *, max_entries: int) -> int:
        owns_transaction = not self._connection.in_transaction
        if max_entries < 0:
            raise ValueError("cache max entries must not be negative")
        table = _TABLES[CacheKind(kind)]
        count = int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        remove_count = max(0, count - max_entries)
        if remove_count:
            self._connection.execute(
                f"DELETE FROM {table} WHERE cache_key IN "
                f"(SELECT cache_key FROM {table} ORDER BY touched_at, cache_key LIMIT ?)",
                (remove_count,),
            )
            if owns_transaction:
                self._connection.commit()
        return remove_count

    def clear(self, kind: CacheKind) -> int:
        owns_transaction = not self._connection.in_transaction
        table = _TABLES[CacheKind(kind)]
        count = int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        self._connection.execute(f"DELETE FROM {table}")
        if owns_transaction:
            self._connection.commit()
        return count


__all__ = ["CacheEntry", "CacheKind", "TerminologyCache"]
