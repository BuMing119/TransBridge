"""Mutable delivery ledger for immutable terminology owners."""

from __future__ import annotations

import sqlite3

from transbridge.application.terminology.errors import RepositoryConflictError
from transbridge.application.terminology.models import ArtifactLedgerEntry, ArtifactStatus

from .codec import dumps, loads


class ArtifactLedger:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def put(self, entry: ArtifactLedgerEntry) -> ArtifactLedgerEntry:
        row = self._connection.execute(
            "SELECT owner_ref, kind, payload_json FROM artifact_ledger WHERE artifact_id = ?",
            (entry.artifact_id,),
        ).fetchone()
        if row is not None:
            if str(row["owner_ref"]) != entry.owner_ref or str(row["kind"]) != entry.kind.value:
                raise RepositoryConflictError("artifact identity cannot change owner or kind")
            existing = loads(str(row["payload_json"]), ArtifactLedgerEntry)
            if existing != entry:
                raise RepositoryConflictError("existing artifact ledger state requires a revision CAS update")
            return existing
        self._connection.execute(
            "INSERT INTO artifact_ledger(artifact_id, owner_ref, kind, renderer_version, content_digest, target, "
            "status, retry_count, diagnostic, revision, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.artifact_id,
                entry.owner_ref,
                entry.kind.value,
                entry.renderer_version,
                entry.content_digest,
                entry.target,
                entry.status.value,
                entry.retry_count,
                entry.diagnostic,
                entry.revision,
                dumps(entry),
            ),
        )
        return entry

    def get(self, artifact_id: str) -> ArtifactLedgerEntry | None:
        row = self._connection.execute(
            "SELECT payload_json FROM artifact_ledger WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        return None if row is None else loads(str(row["payload_json"]), ArtifactLedgerEntry)

    def update(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry:
        current = self.get(entry.artifact_id)
        if current is None:
            raise KeyError(f"artifact ledger entry was not found: {entry.artifact_id}")
        if current.status is not expected_status or current.revision != expected_revision:
            raise RepositoryConflictError(
                f"expected artifact {expected_status.value!r} at revision {expected_revision}, "
                f"found {current.status.value!r} at revision {current.revision}"
            )
        if entry.revision != expected_revision + 1:
            raise RepositoryConflictError("artifact CAS update must advance revision by exactly one")
        if (entry.owner_ref, entry.kind, entry.renderer_version, entry.content_digest) != (
            current.owner_ref,
            current.kind,
            current.renderer_version,
            current.content_digest,
        ):
            raise RepositoryConflictError("artifact CAS update cannot change immutable identity fields")
        _validate_transition(current, entry)
        cursor = self._connection.execute(
            "UPDATE artifact_ledger SET target = ?, status = ?, retry_count = ?, diagnostic = ?, revision = ?, "
            "payload_json = ? WHERE artifact_id = ? AND status = ? AND revision = ?",
            (
                entry.target,
                entry.status.value,
                entry.retry_count,
                entry.diagnostic,
                entry.revision,
                dumps(entry),
                entry.artifact_id,
                expected_status.value,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflictError("artifact ledger revision changed during CAS update")
        return entry


def _validate_transition(current: ArtifactLedgerEntry, updated: ArtifactLedgerEntry) -> None:
    transition = current.status, updated.status
    allowed = {
        (ArtifactStatus.PENDING, ArtifactStatus.RENDERING),
        (ArtifactStatus.FAILED, ArtifactStatus.RENDERING),
        (ArtifactStatus.RENDERING, ArtifactStatus.SUCCEEDED),
        (ArtifactStatus.RENDERING, ArtifactStatus.FAILED),
    }
    if transition not in allowed:
        raise RepositoryConflictError(
            f"artifact transition {current.status.value!r} -> {updated.status.value!r} is not allowed"
        )
    expected_retry = current.retry_count + (
        1 if current.status is ArtifactStatus.RENDERING and updated.status is ArtifactStatus.FAILED else 0
    )
    if updated.retry_count != expected_retry:
        raise RepositoryConflictError("artifact retry count does not match the state transition")


__all__ = ["ArtifactLedger"]
