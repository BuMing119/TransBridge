from dataclasses import replace
from io import BytesIO
from pathlib import Path
import struct
import zlib

import pytest

from tests.dialogue_catalog_support import dialogue_plugin_bytes, field, plugin_bytes, record, reference
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot, plugin_dialogue_order as snapshots
from transbridge.application.io.plugin_dialogue_catalog import (
    METADATA_KEY as CATALOG_METADATA_KEY,
    capture_dialogue_catalog_metadata,
)
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.parser.plugin.dialogue_order_evidence import (
    EXTRACTION_VERSION,
    DialogueOrderEvidence,
    evidence_from_payload,
    evidence_payload,
    read_dialogue_order_bytes,
    read_dialogue_order_evidence,
)
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


def snapshot(content=None):
    return SourceSnapshot.from_bytes(
        SourceDescriptor("Z:/removed/plugin.esp"),
        FormatId.PLUGIN_SSE,
        dialogue_plugin_bytes() if content is None else content,
    )


def scene_values(evidence):
    return [(item.form_id, item.quest_id, item.topic_ids) for item in evidence.scenes]


def test_scene_evidence_reuses_catalog_action_rules_and_round_trips():
    content = dialogue_plugin_bytes()
    plugin = SSEPluginWithContext.from_stream(BytesIO(content), "scene.esp")
    evidence = read_dialogue_order_evidence(plugin)
    assert evidence == read_dialogue_order_bytes(content)
    assert scene_values(evidence) == [
        ("00000012", "00000001", ("00000010", "00000011")),
        ("00000013", "00000001", ()),
    ]
    assert [item.record_ordinal for item in evidence.scenes] == [4, 5]
    assert evidence_from_payload(evidence_payload(evidence)) == evidence
    assert not any(item.kind == "SCEN" for item in evidence.records)


@pytest.mark.parametrize("compressed", [False, True])
def test_physical_scene_and_action_order_are_preserved_without_sorting_form_ids(compressed):
    payload = (
        reference("DATA", 0x99)
        + reference("PNAM", 1)
        + field("ANAM", b"\0\0")
        + reference("DATA", 0x30)
        + reference("DATA", 0x20)
        + reference("DATA", 0x30)
        + field("ANAM", b"")
        + field("ANAM", b"\0")  # Malformed action never supplies a topic/quest.
        + reference("PNAM", 0xBAD)
        + reference("DATA", 0xBAD)
    )
    scene = record("SCEN", 0x50, payload)
    if compressed:
        packed = struct.pack("<I", len(payload)) + zlib.compress(payload)
        scene = struct.pack("<4sIIIIHH", b"SCEN", len(packed), 0x40000, 0x50, 0, 44, 0) + packed
    content = plugin_bytes({"SCEN": scene + record("SCEN", 0x40, reference("PNAM", 1))})
    evidence = read_dialogue_order_bytes(content)
    assert scene_values(evidence) == [
        ("00000050", "00000001", ("00000030", "00000020")),
        ("00000040", "00000001", ()),
    ]
    plugin = SSEPluginWithContext.from_stream(BytesIO(content), "scene.esp")
    assert read_dialogue_order_evidence(plugin) == evidence


def test_deleted_or_duplicate_scenes_do_not_assert_membership():
    deleted = record("SCEN", 0x10, reference("PNAM", 1))
    deleted = deleted[:8] + struct.pack("<I", 0x20) + deleted[12:]
    content = plugin_bytes({
        "SCEN": deleted + record("SCEN", 0x20, b"") + record("SCEN", 0x20, b"") + record("SCEN", 0x30, b""),
    })
    assert [item.form_id for item in read_dialogue_order_bytes(content).scenes] == ["00000030"]


def test_large_unrelated_scene_payload_is_not_retained_in_evidence():
    payload = reference("PNAM", 1) + field("ANAM", b"\0\0") + reference("DATA", 0x10) + field("ANAM", b"")
    plain = plugin_bytes({"SCEN": record("SCEN", 0x12, payload)})
    unrelated = field("XXXX", struct.pack("<I", 100_000)) + field("JUNK", b"") + b"x" * 100_000
    large = plugin_bytes({"SCEN": record("SCEN", 0x12, unrelated + payload)})
    assert evidence_payload(read_dialogue_order_bytes(large)) == evidence_payload(read_dialogue_order_bytes(plain))


def test_v1_bytes_ignore_stale_binary_and_metadata_caches(monkeypatch):
    source = snapshot()
    legacy = evidence_payload(read_dialogue_order_bytes(source.content))[:7]
    old = evidence_from_payload(legacy)
    source = replace(source, metadata=((snapshots.METADATA_KEY, (1, source.sha256, legacy)),))
    monkeypatch.setattr(snapshots, "_cache", snapshots.OrderedDict({(source.sha256, 1): old}))
    monkeypatch.setattr(
        snapshots, "_metadata_cache", snapshots.OrderedDict({(source.sha256, 1, id(legacy)): (legacy, old)})
    )
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("Historical source must not be opened"))
    evidence = snapshots.dialogue_order_from_snapshot(source)
    assert evidence.scenes
    assert (source.sha256, EXTRACTION_VERSION) in snapshots._cache


@pytest.mark.parametrize("with_catalog", [False, True])
def test_metadata_only_v1_retains_graph_and_optionally_recovers_catalog_scenes(with_catalog, monkeypatch):
    source = snapshot()
    plugin = SSEPluginWithContext.from_stream(BytesIO(source.content), "scene.esp")
    evidence = read_dialogue_order_evidence(plugin)
    metadata = ((snapshots.METADATA_KEY, (1, source.sha256, evidence_payload(evidence)[:7])),)
    if with_catalog:
        metadata += (capture_dialogue_catalog_metadata(plugin, source.sha256),)
    source = replace(source, content=None, lease_id="saved-scene-evidence", metadata=metadata)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("Historical source must not be opened"))
    recovered = snapshots.dialogue_order_from_snapshot(source)
    assert recovered.complete and recovered.records == evidence.records
    assert scene_values(recovered) == (scene_values(evidence) if with_catalog else [])


def test_fresh_metadata_retains_scenes_without_binary_or_cache(monkeypatch):
    source = snapshot()
    plugin = SSEPluginWithContext.from_stream(BytesIO(source.content), "scene.esp")
    metadata = (snapshots.capture_dialogue_order_metadata(plugin, source.sha256),)
    source = replace(source, content=None, lease_id="saved-scene-evidence", metadata=metadata)
    monkeypatch.setattr(snapshots, "_cache", snapshots.OrderedDict())
    monkeypatch.setattr(snapshots, "_metadata_cache", snapshots.OrderedDict())
    monkeypatch.setattr(snapshots, "read_dialogue_order_bytes", lambda *_: pytest.fail("Unexpected binary scan"))
    assert scene_values(snapshots.dialogue_order_from_snapshot(source)) == scene_values(
        read_dialogue_order_evidence(plugin)
    )


def test_v1_bytes_do_not_revalidate_obsolete_metadata_after_upgrade(monkeypatch):
    source = snapshot()
    evidence = read_dialogue_order_bytes(source.content)
    source = replace(source, metadata=((snapshots.METADATA_KEY, (1, source.sha256, (None,) * 7)),))
    monkeypatch.setattr(snapshots, "_cache", snapshots.OrderedDict({(source.sha256, EXTRACTION_VERSION): evidence}))
    monkeypatch.setattr(
        snapshots, "evidence_from_payload", lambda *_: pytest.fail("Unexpected old metadata validation")
    )
    assert snapshots.dialogue_order_from_snapshot(source) is evidence


def test_invalid_optional_catalog_keeps_valid_v1_graph():
    source = snapshot()
    evidence = read_dialogue_order_bytes(source.content)
    source = replace(
        source,
        content=None,
        lease_id="saved-scene-evidence",
        metadata=(
            (snapshots.METADATA_KEY, (1, source.sha256, evidence_payload(evidence)[:7])),
            (CATALOG_METADATA_KEY, (1, source.sha256, (("invalid",),))),
        ),
    )
    recovered = snapshots.dialogue_order_from_snapshot(source)
    assert recovered.complete and recovered.records == evidence.records
    assert not recovered.scenes
    assert "DIALOGUE_ORDER_SCENE_CATALOG_INVALID" in {item.code for item in recovered.diagnostics}


@pytest.mark.parametrize(
    "scenes",
    [
        (("00000012", -1, "", ()),),
        (("00000012", 1, "bad", ()),),
        (("00000012", 1, "", ("bad",)),),
        (("00000012", 1, "", ("00000010", "00000010")),),
        (("00000012", 2, "", ()), ("00000013", 1, "", ())),
        (("00000012", 1, "", ()), ("00000012", 2, "", ())),
    ],
)
def test_invalid_scene_metadata_is_rejected(scenes):
    payload = (*evidence_payload(DialogueOrderEvidence())[:7], scenes)
    with pytest.raises(ValueError, match="scene"):
        evidence_from_payload(payload)


def test_scene_extraction_observes_cancellation_and_does_not_publish_partial_cache(monkeypatch):
    source = snapshot(plugin_bytes({"SCEN": b"".join(record("SCEN", index, b"") for index in range(1, 100))}))
    monkeypatch.setattr(snapshots, "_cache", snapshots.OrderedDict())

    class Cancellation:
        checks = 0

        @property
        def is_cancelled(self):
            self.checks += 1
            return self.checks > 50

    with pytest.raises(DialogueOrderCancelled):
        snapshots.dialogue_order_from_snapshot(source, cancellation=Cancellation())
    assert not snapshots._cache
