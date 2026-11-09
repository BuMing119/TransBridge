# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compare-and-swap edits and their durable, retryable receipts."""

from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from typing import Any

from transbridge.application.io.identity import ExternalEntryRef, Provenance
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.storage_contracts import SaveBatch, SaveReceipt
from transbridge.persistence.v2.variant import VariantEntryState

from .entry_codec import DEFAULT_STATE_JOIN, EFFECTIVE_STATE_FIELDS, decode_state, encode_json
from .format import ProjectStorageConflict
from .reads import entry_lookup, require_variant
from .writes import write_state


def read_revisions(connection: sqlite3.Connection, batch: SaveBatch) -> tuple[int, int]:
    project = connection.execute("SELECT identity, revision FROM project").fetchone()
    if project["identity"] != batch.project_id.value:
        raise ProjectStorageConflict("save targets a different project")
    return project["revision"], require_variant(connection, batch.variant_id)["revision"]


def commit_changes(connection: sqlite3.Connection, batch: SaveBatch) -> SaveReceipt:
    project_revision, variant_revision = read_revisions(connection, batch)
    request = None
    if batch.operation_id is not None:
        request = _request_payload(batch)
        previous = connection.execute(
            "SELECT * FROM commit_receipt WHERE operation_id = ?", (batch.operation_id,)
        ).fetchone()
        if previous is not None:
            if previous["request"] != request or previous["variant_id"] != batch.variant_id.value:
                raise ProjectStorageConflict("operation ID already identifies a different save")
            return SaveReceipt(
                batch.project_id,
                batch.variant_id,
                previous["project_revision"],
                previous["variant_revision"],
                tuple(VariantEntryState.from_dict(item) for item in json.loads(previous["result"])),
                batch.operation_id,
                batch.capture_token,
            )
    if (project_revision, variant_revision) != (batch.expected_project_revision, batch.expected_variant_revision):
        raise ProjectStorageConflict(
            f"project or Variant revision changed (project={project_revision}, Variant={variant_revision})"
        )
    changed = []
    for patch in batch.changes:
        row = connection.execute(
            f"""
            SELECT e.id, {EFFECTIVE_STATE_FIELDS} FROM project_source AS ps
            JOIN source_entry AS e ON e.source_id = ps.source_id
            LEFT JOIN entry_state AS vs ON vs.entry_id = e.id AND vs.variant_id = ?
            {DEFAULT_STATE_JOIN}
            WHERE ps.namespace = ? AND e.local_key = ? AND e.has_identity_original = ? AND e.identity_original = ?
            """,
            (batch.variant_id.value, *entry_lookup(patch.key)),
        ).fetchone()
        if row is None:
            raise ProjectStorageConflict(f"unknown source entry {patch.key.serialize()}")
        current = decode_state(row, patch.key)
        if patch.expected_revision is not None and patch.expected_revision != current.revision:
            raise ProjectStorageConflict(f"entry revision changed for {patch.key.serialize()}")
        updated = replace(current, **dict(patch.values))
        if updated != current:
            updated = replace(updated, revision=current.revision.next())
            write_state(connection, batch.variant_id, row["id"], updated)
            changed.append(updated)
    if changed:
        project_revision += 1
        variant_revision += 1
        connection.execute("UPDATE project SET revision = ?", (project_revision,))
        connection.execute(
            "UPDATE variant SET revision = ? WHERE identity = ?", (variant_revision, batch.variant_id.value)
        )
    if batch.operation_id is not None:
        connection.execute(
            """
            INSERT INTO commit_receipt VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.operation_id,
                batch.variant_id.value,
                batch.expected_project_revision,
                batch.expected_variant_revision,
                project_revision,
                variant_revision,
                request,
                encode_json([state.to_dict() for state in changed]),
            ),
        )
    return SaveReceipt(
        batch.project_id,
        batch.variant_id,
        project_revision,
        variant_revision,
        tuple(changed),
        batch.operation_id,
        batch.capture_token,
    )


def _request_payload(batch: SaveBatch) -> str:
    return encode_json([
        batch.expected_project_revision,
        batch.expected_variant_revision,
        [
            [
                change.key.to_dict(),
                None if change.expected_revision is None else change.expected_revision.value,
                {name: _field_payload(value) for name, value in change.values},
            ]
            for change in sorted(batch.changes, key=lambda item: item.key)
        ],
    ])


def _field_payload(value: Any) -> Any:
    if isinstance(value, Stage):
        return value.value
    if isinstance(value, (Provenance, ExternalEntryRef)):
        return value.to_dict()
    if isinstance(value, tuple):
        return tuple(_field_payload(item) for item in value)
    return value
