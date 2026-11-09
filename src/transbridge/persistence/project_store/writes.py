"""Initial project publication and shared row mutation statements."""

from __future__ import annotations

import sqlite3

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.application.io.plugin_dialogue_order import dialogue_order_from_snapshot
from transbridge.application.projects.source_extraction import reconstructed_source_snapshot
from transbridge.application.projects.source_fields import format_fields, parse_options, source_name
from transbridge.application.projects.storage_contracts import ExtractedSource
from transbridge.application.projects.translation_templates import XML_FORMATS
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState

from .entry_codec import STATE_FIELDS, encode_json, entry_values, pack_text, state_values
from .format import PROJECT_FORMAT


def insert_project(
    connection: sqlite3.Connection,
    project_id: ProjectId,
    name: str,
    variant_id: VariantId,
    variant_name: str,
    sources: tuple[ExtractedSource, ...],
) -> None:
    if not name.strip() or not variant_name.strip():
        raise ValueError("project and Variant names must not be empty")
    connection.execute(
        "INSERT INTO variant (identity, name, ordinal, revision) VALUES (?, ?, 0, 0)",
        (variant_id.value, pack_text(variant_name)),
    )
    connection.execute(
        "INSERT INTO project (singleton, format, identity, name, revision, active_variant) VALUES (1, ?, ?, ?, 0, ?)",
        (PROJECT_FORMAT, project_id.value, pack_text(name), variant_id.value),
    )
    for ordinal, source in enumerate(sources):
        insert_source(connection, source, ordinal)


def insert_source(connection: sqlite3.Connection, source: ExtractedSource, ordinal: int) -> None:
    SourceFingerprint(source.namespace, source.fingerprint)
    if source_name(source.name) != source.name:
        raise ValueError("persistent source name cannot contain a path")
    source_id = connection.execute(
        """INSERT INTO source (namespace, name, format, fingerprint, entry_count, options, format_fields)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            source.namespace.value,
            pack_text(source.name),
            source.format_id.value,
            source.fingerprint,
            len(source.entries),
            encode_json(parse_options(source.options)),
            encode_json(format_fields(source.format_fields)),
        ),
    ).lastrowid
    connection.execute(
        "INSERT INTO project_source (source_id, namespace, ordinal) VALUES (?, ?, ?)",
        (source_id, source.namespace.value, ordinal),
    )
    if source.format_records and source.format_id not in XML_FORMATS:
        raise ValueError("physical XML translation records require an XML source")
    connection.executemany(
        "INSERT INTO source_format_record VALUES (?, ?, ?, ?)",
        ((source_id, index, record, tail) for index, (record, tail) in enumerate(source.format_records)),
    )
    reading_ranks = _source_reading_ranks(source)
    for entry_ordinal, entry in enumerate(source.entries):
        key = entry.snapshot.entry_key
        if key.namespace != source.namespace:
            raise ValueError("entry identity does not belong to its source")
        entry_id = connection.execute(
            """
            INSERT INTO source_entry (
                source_id, ordinal, reading_ordinal, local_key, identity_original, has_identity_original,
                legacy_id, original, context, string_id, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                entry_ordinal,
                reading_ranks.get(key, entry_ordinal) if reading_ranks is not None else None,
                *entry_values(entry),
            ),
        ).lastrowid
        if entry.state != VariantEntryState(key):
            connection.execute(
                f"INSERT INTO entry_default (entry_id, {STATE_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, *state_values(entry.state)),
            )


def _source_reading_ranks(source: ExtractedSource):
    if source.format_id is not FormatId.PLUGIN_SSE:
        return None
    snapshot = reconstructed_source_snapshot(source.info, ())
    evidence = dialogue_order_from_snapshot(snapshot)
    if evidence is None or not evidence.complete:
        return {}
    entries = (
        ReadingEntry(row.snapshot.entry_key, row.snapshot.context, row.snapshot.metadata) for row in source.entries
    )
    view = build_dialogue_reading_view(entries, evidence)
    if len(view.ordered_keys) != len(source.entries):
        raise ValueError("reading order must cover the entire plugin translation range")
    return {key: index for index, key in enumerate(view.ordered_keys)}


def write_state(connection: sqlite3.Connection, variant: VariantId, entry_id: int, state: VariantEntryState) -> None:
    connection.execute(
        f"""
        INSERT INTO entry_state (variant_id, entry_id, {STATE_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (variant_id, entry_id) DO UPDATE SET
            translation = excluded.translation, stage = excluded.stage, labels = excluded.labels,
            provenance = excluded.provenance, revision = excluded.revision, tombstone = excluded.tombstone,
            inferred_fields = excluded.inferred_fields, external_refs = excluded.external_refs
        """,
        (variant.value, entry_id, *state_values(state)),
    )
