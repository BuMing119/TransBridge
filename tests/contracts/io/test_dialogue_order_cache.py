from dataclasses import replace

import pytest

from tests.dialogue_order_support import dialogue_bytes
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot, plugin_dialogue_order as authority
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.parser.plugin.dialogue_order_evidence import evidence_payload, read_dialogue_order_bytes


def source_with_metadata():
    content = dialogue_bytes()
    source = SourceSnapshot.from_bytes(SourceDescriptor("Z:/missing.esp"), FormatId.PLUGIN_SSE, content)
    evidence = read_dialogue_order_bytes(content)
    payload = evidence_payload(evidence)
    envelope = authority.EXTRACTION_VERSION, source.sha256, payload
    return replace(source, metadata=((authority.METADATA_KEY, envelope),)), evidence


def test_validated_metadata_reuses_evidence_without_owning_source_bytes(monkeypatch):
    source, expected = source_with_metadata()
    evidence = authority.dialogue_order_from_snapshot(source)
    assert evidence == expected
    monkeypatch.setattr(authority, "evidence_from_payload", lambda *_: pytest.fail("Repeated metadata decode"))
    assert authority.dialogue_order_from_snapshot(replace(source, source=SourceDescriptor("Y:/moved.esp"))) is evidence
    assert all(type(payload) is tuple for payload, _ in authority._metadata_cache.values())


def test_same_digest_different_payload_cannot_reuse_validated_metadata():
    source, _ = source_with_metadata()
    authority.dialogue_order_from_snapshot(source)
    corrupt = replace(source, metadata=((authority.METADATA_KEY, (1, source.sha256, ())),))
    evidence = authority.dialogue_order_from_snapshot(corrupt)
    assert "DIALOGUE_ORDER_METADATA_INVALID" in {item.code for item in evidence.diagnostics}


def test_mutable_legacy_metadata_is_revalidated_after_mutation():
    source, _ = source_with_metadata()
    version, digest, original = dict(source.metadata)[authority.METADATA_KEY]
    records = [list(record) for record in original[0]]
    payload = (records, *original[1:])
    source = replace(source, metadata=((authority.METADATA_KEY, (version, digest, payload)),))
    assert authority.dialogue_order_from_snapshot(source).complete
    records[0][0] = "INVALID"
    evidence = authority.dialogue_order_from_snapshot(source)
    assert "DIALOGUE_ORDER_METADATA_INVALID" in {item.code for item in evidence.diagnostics}


def test_metadata_hit_still_observes_cancellation():
    source, _ = source_with_metadata()
    authority.dialogue_order_from_snapshot(source)
    token = type("Cancelled", (), {"is_cancelled": True})()
    with pytest.raises(DialogueOrderCancelled):
        authority.dialogue_order_from_snapshot(source, cancellation=token)


def test_metadata_cache_evicts_old_payloads(monkeypatch):
    monkeypatch.setattr(authority, "_CACHE_LIMIT", 1)
    first, _ = source_with_metadata()
    second, _ = source_with_metadata()
    one = authority.dialogue_order_from_snapshot(first)
    two = authority.dialogue_order_from_snapshot(second)
    assert one == two and one is not two
    assert len(authority._metadata_cache) == 1
    assert authority.dialogue_order_from_snapshot(first) is not one
