# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Task application evidence stored with the exact project values it confirms."""

from __future__ import annotations

import json
import sqlite3

from transbridge.application.io.identity import EntryKey
from transbridge.application.projects.models import LifecycleSave
from transbridge.application.projects.storage_contracts import ApplicationOutcome, SaveReceipt
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState

from .entry_codec import encode_json
from .format import ProjectStorageConflict
from .reads import read_entries


def receipt_row(connection: sqlite3.Connection, operation_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT p.identity, r.* FROM commit_receipt AS r CROSS JOIN project AS p WHERE r.operation_id = ?
        """,
        (operation_id,),
    ).fetchone()


def decode_receipt(row: sqlite3.Row, capture_token: str | None = None) -> SaveReceipt:
    payload = json.loads(row["result"])
    entries = payload["entries"] if isinstance(payload, dict) else payload
    outcomes = payload.get("application_outcomes", ()) if isinstance(payload, dict) else ()
    return SaveReceipt(
        ProjectId(row["identity"]),
        VariantId(row["variant_id"]),
        row["project_revision"],
        row["variant_revision"],
        tuple(VariantEntryState.from_dict(item) for item in entries),
        row["operation_id"],
        capture_token,
        tuple(
            (EntryKey.from_dict(key), disposition, translation, stage)
            for key, disposition, translation, stage in outcomes
        ),
    )


def record_applications(connection: sqlite3.Connection, save: LifecycleSave) -> None:
    snapshot = save.variant
    if snapshot is None or not save.applications:
        return
    variant = snapshot.ref.identity
    for operation_id, outcomes in save.applications:
        request = encode_json(request_payload(outcomes))
        previous = receipt_row(connection, operation_id)
        if previous is not None:
            if previous["variant_id"] != variant.value or previous["request"] != request:
                raise ProjectStorageConflict("operation ID already identifies a different task application")
            continue
        actual = {
            entry.state.entry_key: entry.state
            for entry in read_entries(connection, variant, (key for key, _, _, _ in outcomes))
        }
        confirmed = []
        entries = []
        for key, disposition, translation, stage in outcomes:
            state = actual.get(key)
            if disposition in {"applied", "unchanged"}:
                if state is None or state.tombstone or (state.translation, state.stage.value) != (translation, stage):
                    disposition = "discarded-or-superseded"
                else:
                    entries.append(state.to_dict())
            confirmed.append((key, disposition, translation, stage))
        connection.execute(
            "INSERT INTO commit_receipt VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                variant.value,
                save.expected_persisted_project_revision,
                save.expected_persisted_revision,
                save.project.envelope.revision,
                snapshot.revision,
                request,
                encode_json({"entries": entries, "application_outcomes": request_payload(confirmed)}),
            ),
        )


def request_payload(outcomes: list[ApplicationOutcome] | tuple[ApplicationOutcome, ...]) -> list:
    return [
        (key.to_dict(), disposition, translation, stage)
        for key, disposition, translation, stage in sorted(outcomes, key=lambda outcome: outcome[0])
    ]
