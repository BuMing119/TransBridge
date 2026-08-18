"""Read-only ParaTranz snapshot adapter for synchronization planning."""

from __future__ import annotations

from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.ports.paratranz import CancellationPort, ParaTranzEntry, ParaTranzPort
from transbridge.application.sync.models import RemoteEntrySnapshot, canonical_hash


class ParaTranzRemoteSnapshotAdapter:
    def __init__(self, service: ParaTranzPort) -> None:
        self._service = service

    def fetch(
        self,
        project_id: int,
        namespace: SourceNamespace,
        *,
        limit: int,
        cancellation: CancellationPort | None = None,
    ) -> tuple[RemoteEntrySnapshot, ...]:
        entries = self._service.list_entries(
            project_id,
            limit=limit + 1,
            cancellation=cancellation,
        )
        if len(entries) > limit:
            raise ValueError("remote snapshot exceeds the configured complete-plan limit")
        scope = f"project:{project_id}"
        return tuple(_snapshot(entry, namespace, scope) for entry in entries)


def _snapshot(
    entry: ParaTranzEntry,
    namespace: SourceNamespace,
    scope: str,
) -> RemoteEntrySnapshot:
    payload = {
        "id": entry.remote_id,
        "key": entry.key,
        "original": entry.original,
        "translation": entry.translation,
        "context": entry.context,
        "stage": entry.stage,
    }
    reference = None if entry.remote_id is None else ExternalEntryRef("paratranz", scope, entry.remote_id)
    return RemoteEntrySnapshot(
        entry_key=EntryKey(namespace, entry.key),
        remote_revision=canonical_hash(payload),
        original=entry.original,
        translation=entry.translation,
        context=entry.context,
        stage=entry.stage,
        external_ref=reference,
    )
