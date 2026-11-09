"""Project reads ordered by source and physical entry position."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import batched
import json
import sqlite3

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.storage_contracts import ProjectSummary, SourceInfo, StoredEntry, VariantInfo
from transbridge.persistence.v2.ids import ProjectId, VariantId

from .entry_codec import (
    DEFAULT_STATE_JOIN,
    EFFECTIVE_STATE_FIELDS,
    VISIBLE_STATE,
    decode_entry,
    decode_key,
    decode_state,
    immutable_json,
    pack_text,
    unpack_text,
)
from .format import ProjectStoreError

ENTRY_COLUMNS = f"""
    ps.namespace, e.local_key, e.identity_original, e.has_identity_original,
    e.legacy_id, e.original, e.context, e.string_id, e.metadata,
    {EFFECTIVE_STATE_FIELDS}
"""


def read_summary(connection: sqlite3.Connection) -> ProjectSummary:
    project = connection.execute("SELECT identity, name, revision, active_variant FROM project").fetchone()
    variants = connection.execute("SELECT identity, name, revision FROM variant ORDER BY ordinal")
    return ProjectSummary(
        ProjectId(project["identity"]),
        unpack_text(project["name"]),
        project["revision"],
        VariantId(project["active_variant"]),
        tuple(VariantInfo(VariantId(row["identity"]), unpack_text(row["name"]), row["revision"]) for row in variants),
    )


def read_sources(
    connection: sqlite3.Connection, namespace: SourceNamespace | None = None, *, native_fields=True
) -> tuple[SourceInfo, ...]:
    source_filter = "" if namespace is None else " WHERE ps.namespace = ?"
    args = () if namespace is None else (namespace.value,)
    fields = "s.format_fields" if native_fields else "'{}'"
    return tuple(
        SourceInfo(
            SourceNamespace(row["namespace"]),
            unpack_text(row["name"]),
            FormatId(row["format"]),
            row["fingerprint"],
            tuple(json.loads(row["options"]).items()),
            tuple((key, immutable_json(value)) for key, value in json.loads(row["format_fields"]).items()),
        )
        for row in connection.execute(
            f"""
            SELECT s.namespace, s.name, s.format, s.fingerprint, s.options, {fields} AS format_fields
            FROM project_source AS ps JOIN source AS s ON s.id = ps.source_id {source_filter} ORDER BY ps.ordinal
            """,
            args,
        )
    )


def read_format_records(
    connection: sqlite3.Connection, namespace: SourceNamespace
) -> tuple[tuple[str, str | None], ...]:
    rows = connection.execute(
        """
        SELECT r.record, r.tail FROM source_format_record AS r
        JOIN project_source AS ps ON ps.source_id = r.source_id WHERE ps.namespace = ? ORDER BY r.ordinal
        """,
        (namespace.value,),
    )
    return tuple((row["record"], row["tail"]) for row in rows)


def iter_source_entries(
    connection: sqlite3.Connection, variant: VariantId, namespace: SourceNamespace, *, include_removed=False
):
    require_variant(connection, variant)
    visibility = "" if include_removed else f" AND {VISIBLE_STATE}"
    rows = connection.execute(
        f"""
        SELECT {ENTRY_COLUMNS} FROM project_source AS ps
        JOIN source_entry AS e ON e.source_id = ps.source_id
        LEFT JOIN entry_state AS vs ON vs.variant_id = ? AND vs.entry_id = e.id
        {DEFAULT_STATE_JOIN}
        WHERE ps.namespace = ?{visibility}
        ORDER BY e.ordinal
        """,
        (variant.value, namespace.value),
    )
    yield from (decode_entry(row) for row in rows)


def read_cursor_page(
    connection: sqlite3.Connection,
    variant: VariantId,
    namespace: SourceNamespace,
    after: int,
    *,
    include_removed=False,
    reading_order=False,
) -> tuple[tuple[StoredEntry, ...], int]:
    visibility = "" if include_removed else f" AND {VISIBLE_STATE}"
    ordinal = "e.reading_ordinal" if reading_order else "e.ordinal"
    rows = tuple(
        connection.execute(
            f"""
        SELECT {ENTRY_COLUMNS}, {ordinal} AS ordinal FROM project_source AS ps
        JOIN source_entry AS e ON e.source_id = ps.source_id
        LEFT JOIN entry_state AS vs ON vs.variant_id = ? AND vs.entry_id = e.id
        {DEFAULT_STATE_JOIN}
        WHERE ps.namespace = ? AND {ordinal} > ?{visibility}
        ORDER BY {ordinal} LIMIT 1000
        """,
            (variant.value, namespace.value, after),
        )
    )
    return tuple(decode_entry(row) for row in rows), after if not rows else rows[-1]["ordinal"]


def read_page(
    connection: sqlite3.Connection,
    variant: VariantId,
    namespace: SourceNamespace | None,
    offset: int,
    limit: int,
    *,
    include_removed: bool = False,
    reading_order: bool = False,
) -> tuple[StoredEntry, ...]:
    require_variant(connection, variant)
    sources_sql = "SELECT source_id, namespace FROM project_source"
    parameters = ()
    if namespace is not None:
        sources_sql += " WHERE namespace = ?"
        parameters = (namespace.value,)
    sources = connection.execute(sources_sql + " ORDER BY ordinal", parameters)
    entry_columns = ENTRY_COLUMNS.replace("ps.namespace", "? AS namespace", 1)
    visibility = "" if include_removed else f" AND {VISIBLE_STATE}"
    ordinal = "e.reading_ordinal" if reading_order else "e.ordinal"
    reading_filter = " AND e.reading_ordinal IS NOT NULL" if reading_order else ""
    entry_query = f"""
        FROM source_entry AS e
        LEFT JOIN entry_state AS vs ON vs.variant_id = ? AND vs.entry_id = e.id
        {DEFAULT_STATE_JOIN}
        WHERE e.source_id = ?{reading_filter}{visibility}
    """
    entries = []
    for source in sources:
        remaining = -1 if limit == -1 else limit - len(entries)
        source_params = (variant.value, source["source_id"])
        rows = connection.execute(
            f"SELECT {entry_columns} {entry_query} ORDER BY {ordinal} LIMIT ? OFFSET ?",
            (source["namespace"], *source_params, remaining, offset),
        )
        page = tuple(decode_entry(row) for row in rows)
        entries.extend(page)
        if limit != -1 and len(entries) == limit:
            break
        if offset:
            if page:
                offset = 0
            else:
                skipped = connection.execute(f"SELECT COUNT(*) {entry_query}", source_params).fetchone()[0]
                offset -= skipped
    return tuple(entries)


def read_entries(
    connection: sqlite3.Connection, variant: VariantId, keys: Iterable[EntryKey]
) -> tuple[StoredEntry, ...]:
    require_variant(connection, variant)
    entries = []
    for chunk in batched(dict.fromkeys(keys), 200):
        values = ",".join("(?, ?, ?, ?, ?)" for _ in chunk)
        parameters = tuple(value for ordinal, key in enumerate(chunk) for value in (*entry_lookup(key), ordinal))
        rows = connection.execute(
            f"""
            WITH requested(namespace, local_key, has_original, original, ordinal) AS (VALUES {values})
            SELECT {ENTRY_COLUMNS} FROM requested AS r
            JOIN project_source AS ps ON ps.namespace = r.namespace
            JOIN source_entry AS e ON e.source_id = ps.source_id AND e.local_key = r.local_key
                AND e.has_identity_original = r.has_original AND e.identity_original = r.original
            LEFT JOIN entry_state AS vs ON vs.variant_id = ? AND vs.entry_id = e.id
            {DEFAULT_STATE_JOIN}
            ORDER BY r.ordinal
            """,
            (*parameters, variant.value),
        )
        entries.extend(decode_entry(row) for row in rows)
    return tuple(entries)


def read_baseline_states(connection: sqlite3.Connection, namespace: SourceNamespace):
    rows = connection.execute(
        f"""
        SELECT ps.namespace, e.local_key, e.identity_original, e.has_identity_original, ds.*
        FROM project_source AS ps JOIN source_entry AS e ON e.source_id = ps.source_id
        {DEFAULT_STATE_JOIN}
        WHERE ps.namespace = ? ORDER BY e.ordinal
        """,
        (namespace.value,),
    )
    return tuple(decode_state(row, decode_key(row)) for row in rows)


def entry_lookup(key: EntryKey) -> tuple[str, str, int, str]:
    return key.namespace.value, pack_text(key.local_key), int(key.original is not None), pack_text(key.original or "")


def require_variant(connection: sqlite3.Connection, variant: VariantId) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM variant WHERE identity = ?", (variant.value,)).fetchone()
    if row is None:
        raise ProjectStoreError(f"unknown project Variant {variant.value!r}")
    return row
