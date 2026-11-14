# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Variant-owned persisted judgments, published only against unchanged entry fences."""

from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from types import MappingProxyType
from uuid import uuid4

from transbridge.application.io.identity import EntryKey
from transbridge.persistence.v2.ids import VariantId

from . import alignment_cache_schema
from .connection import project_connection
from .entry_codec import pack_text, unpack_text
from .format import ProjectStorageConflict
from .reads import entry_lookup, require_variant


@dataclass(frozen=True, slots=True)
class CacheToken:
    entry_id: int
    nonce: str
    generation: int


@dataclass(frozen=True, slots=True)
class CacheSnapshot:
    project_id: str
    variant_id: str
    tokens: object
    records: object


class ProjectAlignmentCache:
    """One project database; callers serialize and validate domain-specific judgments."""

    def __init__(self, path, *, max_per_entry=16, max_per_variant=10000, max_record_bytes=1024 * 1024):
        if min(max_per_entry, max_per_variant, max_record_bytes) < 1:
            raise ValueError("alignment cache limits must be positive")
        self.path = Path(path)
        self.max_per_entry, self.max_per_variant = max_per_entry, max_per_variant
        self.max_record_bytes = max_record_bytes

    def capture(self, variant_id: str, inputs) -> CacheSnapshot:
        """Capture only persisted matching inputs, with a fence created before model work."""
        tokens, records = {}, {}
        with project_connection(self.path, write=True) as connection:
            require_variant(connection, VariantId(variant_id))
            project_id = connection.execute("SELECT identity FROM project").fetchone()[0]
            alignment_cache_schema.install(connection)
            for entry_key, original, translation in inputs:
                key = EntryKey.deserialize(entry_key)
                row = _input_row(connection, variant_id, key)
                if row is None or (unpack_text(row["original"]), unpack_text(row["translation"])) != (
                    original,
                    translation,
                ):
                    continue
                fence = connection.execute(
                    "SELECT nonce, generation FROM alignment_cache_fence WHERE variant_id = ? AND entry_id = ?",
                    (variant_id, row["id"]),
                ).fetchone()
                if fence is None:
                    nonce = uuid4().hex
                    connection.execute(
                        "INSERT INTO alignment_cache_fence (variant_id, entry_id, nonce) VALUES (?, ?, ?)",
                        (variant_id, row["id"], nonce),
                    )
                    tokens[entry_key] = CacheToken(row["id"], nonce, 0)
                else:
                    tokens[entry_key] = CacheToken(row["id"], fence["nonce"], fence["generation"])
                records[entry_key] = tuple(
                    (unpack_text(item["request_key"]), unpack_text(item["payload"]))
                    for item in connection.execute(
                        "SELECT request_key, payload FROM alignment_cache_record "
                        "WHERE variant_id = ? AND entry_id = ? ORDER BY used DESC, id DESC",
                        (variant_id, row["id"]),
                    )
                )
        return CacheSnapshot(project_id, variant_id, MappingProxyType(tokens), MappingProxyType(records))

    def publish(self, snapshot: CacheSnapshot, records) -> int:
        """Ignore stale/evicted entries; unrelated entries may still publish successfully."""
        published, touched = 0, set()
        with project_connection(self.path, write=True) as connection:
            if connection.execute("SELECT identity FROM project").fetchone()[0] != snapshot.project_id:
                raise ProjectStorageConflict("alignment cache snapshot belongs to another project")
            if not alignment_cache_schema.installed(connection):
                return 0
            for entry_key, request_key, payload in records:
                token = snapshot.tokens.get(entry_key)
                if token is None:
                    continue
                if (
                    len(request_key.encode("utf-8", "surrogatepass")) + len(payload.encode("utf-8", "surrogatepass"))
                    > self.max_record_bytes
                ):
                    continue
                current = connection.execute(
                    "SELECT nonce, generation FROM alignment_cache_fence WHERE variant_id = ? AND entry_id = ?",
                    (snapshot.variant_id, token.entry_id),
                ).fetchone()
                if current is None or (current["nonce"], current["generation"]) != (token.nonce, token.generation):
                    continue
                connection.execute(
                    "INSERT INTO alignment_cache_record (variant_id, entry_id, request_key, payload, used) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (variant_id, entry_id, request_key) DO UPDATE SET "
                    "payload = excluded.payload, used = excluded.used",
                    (snapshot.variant_id, token.entry_id, pack_text(request_key), pack_text(payload), time_ns()),
                )
                touched.add(token.entry_id)
                published += 1
            for entry_id in touched:
                connection.execute(
                    "DELETE FROM alignment_cache_record WHERE id IN (SELECT id FROM alignment_cache_record "
                    "WHERE variant_id = ? AND entry_id = ? ORDER BY used DESC, id DESC LIMIT -1 OFFSET ?)",
                    (snapshot.variant_id, entry_id, self.max_per_entry),
                )
            if touched:
                connection.execute(
                    "DELETE FROM alignment_cache_record WHERE id IN (SELECT id FROM alignment_cache_record "
                    "WHERE variant_id = ? ORDER BY used DESC, id DESC LIMIT -1 OFFSET ?)",
                    (snapshot.variant_id, self.max_per_variant),
                )
        return published

    def valid_entries(self, snapshot: CacheSnapshot) -> frozenset[str]:
        """Recheck captured fences after long-running work without changing any state."""
        valid = []
        with project_connection(self.path) as connection:
            if connection.execute("SELECT identity FROM project").fetchone()[0] != snapshot.project_id:
                raise ProjectStorageConflict("alignment cache snapshot belongs to another project")
            if not alignment_cache_schema.installed(connection):
                return frozenset()
            for entry_key, token in snapshot.tokens.items():
                current = connection.execute(
                    "SELECT nonce, generation FROM alignment_cache_fence WHERE variant_id = ? AND entry_id = ?",
                    (snapshot.variant_id, token.entry_id),
                ).fetchone()
                if current is not None and (current["nonce"], current["generation"]) == (token.nonce, token.generation):
                    valid.append(entry_key)
        return frozenset(valid)

    def clear(self, variant_id: str, entry_keys=None) -> None:
        """Invalidate outstanding tokens as well as saved records, including empty fences."""
        with project_connection(self.path, write=True) as connection:
            if not alignment_cache_schema.installed(connection):
                return
            if entry_keys is None:
                connection.execute(
                    "UPDATE alignment_cache_fence SET generation = generation + 1 WHERE variant_id = ?", (variant_id,)
                )
                connection.execute("DELETE FROM alignment_cache_record WHERE variant_id = ?", (variant_id,))
                return
            for entry_key in entry_keys:
                key = EntryKey.deserialize(entry_key)
                row = _input_row(connection, variant_id, key, include_removed=True)
                if row is not None:
                    connection.execute(
                        "UPDATE alignment_cache_fence SET generation = generation + 1 "
                        "WHERE variant_id = ? AND entry_id = ?",
                        (variant_id, row["id"]),
                    )
                    connection.execute(
                        "DELETE FROM alignment_cache_record WHERE variant_id = ? AND entry_id = ?",
                        (variant_id, row["id"]),
                    )


def _input_row(connection, variant_id, key, *, include_removed=False):
    return connection.execute(
        "SELECT e.id, e.original, COALESCE(vs.translation, ds.translation, '') AS translation "
        "FROM project_source AS ps JOIN source_entry AS e ON e.source_id = ps.source_id "
        "LEFT JOIN entry_state AS vs ON vs.variant_id = ? AND vs.entry_id = e.id "
        "LEFT JOIN entry_default AS ds ON ds.entry_id = e.id "
        "WHERE ps.namespace = ? AND e.local_key = ? AND e.has_identity_original = ? AND e.identity_original = ? "
        + ("" if include_removed else "AND COALESCE(vs.tombstone, ds.tombstone, 0) = 0"),
        (variant_id, *entry_lookup(key)),
    ).fetchone()
