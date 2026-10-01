from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import Mock

import pytest

from tests.contracts.io.test_paratranz_adapter import _parse, _write
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.identity import EntryRevision, SourceNamespace
from transbridge.application.io.paratranz_context_order import PARATRANZ_CONTEXT_ORDER_METADATA
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.sync.models import EntrySummary, LocalEntrySnapshot, canonical_hash
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter


@pytest.mark.parametrize("context", ["INFO:NAM1|00123456", "DIAL:FULL|", "BOOK:FULL"])
@pytest.mark.parametrize("representation", ["dto", "entry", "snapshot", "serialized"])
def test_ordered_context_roundtrip_preserves_wire_data_and_local_classification(tmp_path, context, representation):
    payload = {
        "id": 71,
        "key": "Quest:00123456|1~" + context.split("|")[0],
        "original": "Original",
        "translation": "Translated",
        "stage": 1,
        "context": "00000428|" + context,
        "note": "keep",
    }
    source = tmp_path / "source.json"
    source.write_text(json.dumps([payload]), encoding="utf-8")
    parsed = _parse(source)
    assert parsed.outcome is OperationOutcome.COMPLETED
    dto = parsed.entries[0]
    assert dto.context == payload["context"]
    entry = dto.to_translation_entry()
    assert entry.context == context
    assert dict(entry.metadata)[PARATRANZ_CONTEXT_ORDER_METADATA] == 428
    assert "plugin.source_order" not in dict(entry.metadata)
    output = entry
    if representation == "dto":
        output = dto
    elif representation == "snapshot":
        output = entry.snapshot()
    elif representation == "serialized":
        output = TranslationEntry.from_dict(json.loads(json.dumps(entry.to_dict())))
    target = tmp_path / "out.json"
    result = _write(target, (output,))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert json.loads(target.read_text(encoding="utf-8")) == [payload]
    assert entry.context == context


@pytest.mark.parametrize(
    "context",
    [
        None,
        "",
        "BOOK:FULL",
        "12345678|This is external prose",
        "0000428|BOOK:FULL",
        "000000428|BOOK:FULL",
        "０００００４２８|BOOK:FULL",
        "00000428|INFO:NAM1|not-a-quest-id",
    ],
)
def test_legacy_and_unrecognized_context_is_not_stripped(tmp_path, context):
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps([{"key": "key", "original": "Text", "context": context}]), encoding="utf-8")
    parsed = _parse(source)
    assert parsed.outcome is OperationOutcome.COMPLETED
    entry = parsed.entries[0].to_translation_entry()
    assert entry.context == context
    assert PARATRANZ_CONTEXT_ORDER_METADATA not in dict(entry.metadata)


def test_remote_snapshot_normalizes_context_without_hiding_remote_order_changes():
    remote = ParaTranzEntry(71, "key", "Original", "Translated", "00000428|INFO:NAM1|00123456", 1)
    service = Mock()
    service.list_entries.return_value = (remote,)
    adapter = ParaTranzRemoteSnapshotAdapter(service)
    namespace = SourceNamespace("test:ordered-context")
    snapshot = adapter.fetch(7, namespace, limit=10)[0]
    assert snapshot.context == "INFO:NAM1|00123456"
    assert snapshot.context_order == 428
    assert snapshot.remote_revision == canonical_hash({"id": remote.remote_id, **remote.to_remote_payload()})
    local = LocalEntrySnapshot(
        snapshot.entry_key,
        EntryRevision(),
        remote.original,
        remote.translation,
        snapshot.context,
        remote.stage,
        snapshot.external_ref,
    )
    assert EntrySummary.from_local(local).content_identity() == EntrySummary.from_remote(snapshot).content_identity()
    service.list_entries.return_value = (replace(remote, context="00000429|INFO:NAM1|00123456"),)
    reordered = adapter.fetch(7, namespace, limit=10)[0]
    assert reordered.context == snapshot.context
    assert reordered.context_order == 429
    assert reordered.remote_revision != snapshot.remote_revision
