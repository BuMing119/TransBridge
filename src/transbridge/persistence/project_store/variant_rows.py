# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Transactional persistence of version metadata and the affected entry states."""

from __future__ import annotations

from collections.abc import Iterable
import sqlite3

from transbridge.application.projects.models import LifecycleSave
from transbridge.persistence.v2.variant import VariantEntryState, VariantSnapshot

from .entry_codec import decode_state, encode_json
from .format import ProjectStorageConflict
from .reads import entry_lookup
from .writes import insert_source, write_state


def stored_entry_id(connection: sqlite3.Connection, state: VariantEntryState) -> int:
    row = connection.execute(
        """
        SELECT e.id FROM project_source AS ps JOIN source_entry AS e ON e.source_id = ps.source_id
        WHERE ps.namespace = ? AND e.local_key = ? AND e.has_identity_original = ? AND e.identity_original = ?
        """,
        entry_lookup(state.entry_key),
    ).fetchone()
    if row is None:
        raise ProjectStorageConflict(f"entry is absent from the current source: {state.entry_key.serialize()}")
    return row["id"]


def write_variant(
    connection: sqlite3.Connection,
    snapshot: VariantSnapshot,
    entries: Iterable[VariantEntryState],
    expected_revision: int,
) -> None:
    cursor = connection.execute(
        "UPDATE variant SET revision = ?, label_library = ? WHERE identity = ? AND revision = ?",
        (snapshot.revision, encode_json(snapshot.label_library_data()), snapshot.ref.identity.value, expected_revision),
    )
    if cursor.rowcount != 1:
        raise ProjectStorageConflict("Variant changed before saving")
    for state in entries:
        entry_id = stored_entry_id(connection, state)
        row = connection.execute("SELECT * FROM entry_default WHERE entry_id = ?", (entry_id,)).fetchone()
        default = VariantEntryState(state.entry_key) if row is None else decode_state(row, state.entry_key)
        if state != default:
            write_state(connection, snapshot.ref.identity, entry_id, state)
        else:
            connection.execute(
                "DELETE FROM entry_state WHERE variant_id = ? AND entry_id = ?",
                (snapshot.ref.identity.value, entry_id),
            )


def save_sources(
    connection: sqlite3.Connection, save: LifecycleSave, *, replacements: dict[str, str] | None = None
) -> None:
    snapshot = save.variant
    if snapshot is None:
        return
    desired = {item.namespace.value: item.sha256 for item in snapshot.source_fingerprints}
    current = {
        row["namespace"]: (row["source_id"], row["fingerprint"], row["ordinal"])
        for row in connection.execute("""
            SELECT ps.namespace, ps.source_id, s.fingerprint, ps.ordinal
            FROM project_source AS ps JOIN source AS s ON s.id = ps.source_id
        """)
    }
    added = {
        source.namespace.value: source
        for source in save.source_additions
        if desired.get(source.namespace.value) == source.fingerprint
    }
    changed = {
        namespace
        for namespace, fingerprint in desired.items()
        if namespace not in current or current[namespace][1] != fingerprint
    }
    replacement_ordinals = {new: current[old][2] for old, new in (replacements or {}).items() if old in current}
    # An explicit source revision can change localized text even with identical plugin bytes.
    changed.update(replacement_ordinals)
    if changed - added.keys():
        raise ProjectStorageConflict("source revision lacks extracted translation data")
    for namespace, (source_id, _, _) in current.items():
        if namespace in changed or namespace not in desired:
            connection.execute("DELETE FROM project_source WHERE source_id = ?", (source_id,))
            connection.execute(
                "DELETE FROM entry_state WHERE entry_id IN (SELECT id FROM source_entry WHERE source_id = ?)",
                (source_id,),
            )
    ordinal = connection.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM project_source").fetchone()[0]
    for namespace, source in added.items():
        if namespace not in changed:
            continue
        source_ordinal = replacement_ordinals.get(namespace)
        if source_ordinal is None:
            source_ordinal = ordinal
            ordinal += 1
        insert_source(connection, source, source_ordinal)
    connection.execute("""
        DELETE FROM source WHERE id NOT IN (SELECT source_id FROM project_source)
            AND id NOT IN (SELECT source_id FROM snapshot_source)
    """)
