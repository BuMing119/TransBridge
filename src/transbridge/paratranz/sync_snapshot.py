"""Read-only ParaTranz snapshot adapter for synchronization planning."""

from __future__ import annotations

from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.application.io.paratranz_keys import decode_key
from transbridge.application.ports.paratranz import CancellationPort, ParaTranzEntry, ParaTranzPort
from transbridge.application.sync.models import RemoteEntrySnapshot, canonical_hash


class ParaTranzRemoteSnapshotAdapter:
    def __init__(self, service: ParaTranzPort, *, decode_keys: bool = False) -> None:
        self._service = service
        self._decode_keys = decode_keys

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
        return tuple(_snapshot(entry, namespace, scope, decode_keys=self._decode_keys) for entry in entries)


def _snapshot(
    entry: ParaTranzEntry,
    namespace: SourceNamespace,
    scope: str,
    *,
    decode_keys: bool = False,
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
    context, order = parse_ordered_context(entry.context)
    return RemoteEntrySnapshot(
        entry_key=decode_key(entry.key, entry.original, namespace) if decode_keys else EntryKey(namespace, entry.key),
        remote_revision=canonical_hash(payload),
        original=entry.original,
        translation=entry.translation,
        context=context,
        stage=entry.stage,
        external_ref=reference,
        context_order=order,
    )
