"""Named project snapshots over immutable source revisions."""

from __future__ import annotations

import json
import sqlite3

from transbridge.application.projects.models import LifecycleSnapshot
from transbridge.application.projects.snapshots import ProjectSnapshotInfo
from transbridge.persistence.v2.ids import VariantRef
from transbridge.persistence.v2.variant import SourceFingerprint, VariantSnapshot

from .connection import project_connection
from .entry_codec import (
    DEFAULT_STATE_JOIN,
    STATE_FIELDS,
    decode_entry,
    encode_json,
    pack_text,
    state_values,
    unpack_text,
)
from .format import ProjectStoreError
from .reads import ENTRY_COLUMNS
from .records import WorkspaceProjects
from .variant_rows import stored_entry_id


class ProjectHistory:
    def __init__(self, projects: WorkspaceProjects) -> None:
        self.projects = projects

    def list(self, ref: VariantRef) -> tuple[ProjectSnapshotInfo, ...]:
        with project_connection(self.projects.store_for(ref.project_id).path) as connection:
            rows = connection.execute(
                """
                SELECT identity, name, revision FROM named_snapshot
                WHERE variant_id = ? ORDER BY revision DESC, name DESC, identity DESC
                """,
                (ref.identity.value,),
            )
            return tuple(
                ProjectSnapshotInfo(row["identity"], unpack_text(row["name"]), row["revision"]) for row in rows
            )

    def load(self, identity: str, ref: VariantRef) -> VariantSnapshot:
        with project_connection(self.projects.store_for(ref.project_id).path) as connection:
            return load_snapshot(connection, identity, ref)

    def read_page(self, identity: str, ref: VariantRef, *, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("historical pages require a non-negative offset and 1–1000 entries")
        with project_connection(self.projects.store_for(ref.project_id).path) as connection:
            snapshot_record(connection, identity, ref)
            return tuple(decode_entry(row) for row in snapshot_entry_rows(connection, identity, limit, offset))

    def delete(self, identity: str, ref: VariantRef) -> None:
        with project_connection(self.projects.store_for(ref.project_id).path, write=True) as connection:
            deleted = connection.execute(
                "DELETE FROM named_snapshot WHERE identity = ? AND variant_id = ?", (identity, ref.identity.value)
            )
            if deleted.rowcount != 1:
                raise ProjectStoreError("snapshot does not belong to this Variant")
            connection.execute("""
                DELETE FROM source WHERE id NOT IN (SELECT source_id FROM project_source)
                    AND id NOT IN (SELECT source_id FROM snapshot_source)
            """)


def write_snapshot(connection: sqlite3.Connection, snapshot: LifecycleSnapshot, identity: str) -> None:
    settings = json.loads(connection.execute("SELECT settings FROM project").fetchone()[0])
    sources = {
        key: settings[key] for key in ("sources", "source_relations", "source_registry_diagnostics") if key in settings
    }
    connection.execute(
        "INSERT INTO named_snapshot VALUES (?, ?, ?, ?, ?, ?)",
        (
            identity,
            snapshot.formal_variant_ref.identity.value,
            pack_text(snapshot.name),
            snapshot.variant.revision,
            encode_json(snapshot.variant.label_library_data()),
            encode_json(sources),
        ),
    )
    connection.execute("INSERT INTO snapshot_source SELECT ?, source_id, ordinal FROM project_source", (identity,))
    for state in snapshot.variant.entries:
        connection.execute(
            f"""
            INSERT INTO snapshot_entry_state (snapshot_id, entry_id, {STATE_FIELDS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (identity, stored_entry_id(connection, state), *state_values(state)),
        )


def snapshot_record(connection: sqlite3.Connection, identity: str, ref: VariantRef) -> sqlite3.Row:
    snapshot = connection.execute(
        "SELECT * FROM named_snapshot WHERE identity = ? AND variant_id = ?", (identity, ref.identity.value)
    ).fetchone()
    if snapshot is None:
        raise ProjectStoreError("snapshot does not belong to this Variant")
    return snapshot


def snapshot_entry_rows(connection: sqlite3.Connection, identity: str, limit: int, offset: int):
    return connection.execute(
        f"""
        SELECT {ENTRY_COLUMNS} FROM (
            SELECT s.namespace, ss.source_id, ss.ordinal
            FROM snapshot_source AS ss JOIN source AS s ON s.id = ss.source_id
            WHERE ss.snapshot_id = ?
        ) AS ps JOIN source_entry AS e ON e.source_id = ps.source_id
        LEFT JOIN snapshot_entry_state AS vs ON vs.snapshot_id = ? AND vs.entry_id = e.id
        {DEFAULT_STATE_JOIN}
        ORDER BY ps.ordinal, e.ordinal LIMIT ? OFFSET ?
        """,
        (identity, identity, limit, offset),
    )


def load_snapshot(connection: sqlite3.Connection, identity: str, ref: VariantRef) -> VariantSnapshot:
    snapshot = snapshot_record(connection, identity, ref)
    sources = connection.execute(
        """
        SELECT s.namespace, s.fingerprint FROM snapshot_source AS ss JOIN source AS s ON s.id = ss.source_id
        WHERE ss.snapshot_id = ? ORDER BY ss.ordinal
        """,
        (identity,),
    )
    return VariantSnapshot(
        ref,
        tuple(
            SourceFingerprint.from_dict({"namespace": row["namespace"], "sha256": row["fingerprint"]})
            for row in sources
        ),
        tuple(decode_entry(row).state for row in snapshot_entry_rows(connection, identity, -1, 0)),
        snapshot["revision"],
        tuple(json.loads(snapshot["label_library"]).items()),
    )
