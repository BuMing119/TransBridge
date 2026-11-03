from dataclasses import replace
from io import BytesIO
from pathlib import Path
import struct
import zlib

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot, plugin_dialogue_order as snapshots
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled, binary_records, parsed_records
from transbridge.parser.plugin.dialogue_order_evidence import (
    evidence_from_payload,
    evidence_payload,
    read_dialogue_order_bytes,
    read_dialogue_order_evidence,
)
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


def children(topic, data):
    return struct.pack("<4sIIiHHHH", b"GRUP", len(data) + 24, topic, 7, 0, 0, 0, 0) + data


def fixture(*, target=0x20, second_quest=1, branch_quest=1, compressed=False, deleted_target=False, bnam=None):
    info_fields = (
        field("DATA", b"\0\0") + reference("TCLT", target) + reference("TWAT", 0x999) + reference("TCLT", 0x20)
    )
    info = record("INFO", 0x11, info_fields)
    if compressed:
        payload = struct.pack("<I", len(info_fields)) + zlib.compress(info_fields)
        info = struct.pack("<4sIIIIHH", b"INFO", len(payload), 0x40000, 0x11, 0, 44, 0) + payload
    topic2 = record("DIAL", 0x20, reference("QNAM", second_quest) + (reference("BNAM", 0x30) if bnam is None else bnam))
    if deleted_target:
        topic2 = topic2[:8] + struct.pack("<I", 0x20) + topic2[12:]
    return plugin_bytes({
        "QUST": record("QUST", 1, b"") + record("QUST", 2, b""),
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1)) + children(0x10, info) + topic2,
        "DLBR": record("DLBR", 0x30, reference("QNAM", branch_quest) + reference("SNAM", 0x10)),
        "NPC_": record("NPC_", 0x40, b""),
    })


def snapshot(content=None, *, metadata=()):
    return SourceSnapshot.from_bytes(
        SourceDescriptor("Z:/no-longer-present/plugin.esp"),
        FormatId.PLUGIN_SSE,
        fixture() if content is None else content,
        metadata=metadata,
    )


def codes(evidence):
    return {item.code for item in evidence.diagnostics}


def test_parsed_and_saved_byte_evidence_are_identical_without_output_strings():
    content = fixture()
    parsed = SSEPluginWithContext.from_stream(BytesIO(content), "plugin.esp")
    evidence = read_dialogue_order_evidence(parsed)
    assert evidence == read_dialogue_order_bytes(content)
    records = {item.form_id: item for item in evidence.records}
    assert set(records) == {"00000001", "00000002", "00000010", "00000011", "00000020", "00000030"}
    assert records["00000011"].parent_topic_id == "00000010"
    assert records["00000020"].branch_id == "00000030"
    assert records["00000030"].start_topic_id == "00000010"
    assert [(link.field_index, link.target_topic_id) for link in evidence.links] == [(1, "00000020"), (3, "00000020")]
    assert all(link.valid for link in evidence.links)
    assert not evidence.diagnostics
    assert evidence_from_payload(evidence_payload(evidence)) == evidence


def test_compressed_record_retains_real_tclt_field_order():
    plain = read_dialogue_order_bytes(fixture())
    compressed = read_dialogue_order_bytes(fixture(compressed=True))
    assert compressed == plain
    parsed = SSEPluginWithContext.from_stream(BytesIO(fixture(compressed=True)), "plugin.esp")
    assert read_dialogue_order_evidence(parsed) == plain


@pytest.mark.parametrize(("target", "expected"), [(0x999, "MISSING_REFERENCE"), (0x40, "WRONG_REFERENCE_TYPE")])
def test_bad_tclt_is_diagnosed_and_only_source_quest_falls_back(target, expected):
    evidence = read_dialogue_order_bytes(fixture(target=target))
    assert f"DIALOGUE_ORDER_{expected}" in codes(evidence)
    assert not evidence.links[0].valid
    assert evidence.invalid_quests == ("00000001",)


def test_deleted_target_remains_visible_as_evidence_and_referenced_quest_falls_back():
    evidence = read_dialogue_order_bytes(fixture(deleted_target=True))
    assert "DIALOGUE_ORDER_DELETED_REFERENCE" in codes(evidence)
    assert next(item for item in evidence.records if item.form_id == "00000020").deleted
    assert evidence.invalid_quests == ("00000001",)


def test_cross_quest_tclt_falls_back_both_tasks():
    content = fixture(second_quest=2).replace(reference("BNAM", 0x30), reference("BNAM", 0))
    evidence = read_dialogue_order_bytes(content)
    assert "DIALOGUE_ORDER_CROSS_QUEST_LINK" in codes(evidence)
    assert evidence.invalid_quests == ("00000001", "00000002")
    assert not any(link.valid for link in evidence.links)


def test_conflicting_dial_ownership_is_not_guessed():
    evidence = read_dialogue_order_bytes(fixture(second_quest=2))
    assert "DIALOGUE_ORDER_CONFLICTING_QUEST" in codes(evidence)
    topic = next(item for item in evidence.records if item.form_id == "00000020")
    assert topic.quest_id == "" and not topic.valid
    assert evidence.invalid_quests == ("00000001", "00000002")


def test_bad_branch_start_only_disables_branch_hint():
    content = fixture().replace(reference("SNAM", 0x10), reference("SNAM", 0x999))
    evidence = read_dialogue_order_bytes(content)
    branch = next(item for item in evidence.records if item.kind == "DLBR")
    assert not branch.valid
    assert not evidence.invalid_quests
    assert all(link.valid for link in evidence.links)
    assert evidence.invalid_branch_quests == ("00000001",)


@pytest.mark.parametrize("branch_field", [field("BNAM", b"bad"), reference("BNAM", 0x999)])
def test_bad_bnam_only_disables_branch_preference_with_valid_explicit_quest(branch_field):
    content = fixture(bnam=branch_field)
    evidence = read_dialogue_order_bytes(content)
    assert evidence.invalid_branch_quests == ("00000001",)
    assert not evidence.invalid_quests
    assert all(link.valid for link in evidence.links)


def test_branch_missing_start_still_supplies_explicit_quest_membership():
    content = plugin_bytes({
        "QUST": record("QUST", 1, b""),
        "DIAL": record("DIAL", 0x10, reference("BNAM", 0x30)),
        "DLBR": record("DLBR", 0x30, reference("QNAM", 1)),
    })
    evidence = read_dialogue_order_bytes(content)
    topic = next(item for item in evidence.records if item.kind == "DIAL")
    assert topic.quest_id == "00000001" and topic.valid
    assert not evidence.invalid_quests
    assert evidence.invalid_branch_quests == ("00000001",)


def test_snam_can_associate_a_topic_without_qnam_or_bnam():
    content = plugin_bytes({
        "QUST": record("QUST", 1, b""),
        "DIAL": record("DIAL", 0x10, b""),
        "DLBR": record("DLBR", 0x30, reference("QNAM", 1) + reference("SNAM", 0x10)),
    })
    evidence = read_dialogue_order_bytes(content)
    topic = next(item for item in evidence.records if item.kind == "DIAL")
    assert topic.quest_id == "00000001" and topic.valid
    assert not evidence.invalid_quests


def test_orphan_info_diagnoses_missing_parent_without_guessing_by_record_order():
    content = plugin_bytes({
        "QUST": record("QUST", 1, b""),
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1)) + record("INFO", 0x11, reference("TCLT", 0x10)),
    })
    evidence = read_dialogue_order_bytes(content)
    assert "DIALOGUE_ORDER_MISSING_PARENT" in codes(evidence)
    info = next(item for item in evidence.records if item.kind == "INFO")
    assert not info.valid and not info.parent_topic_id
    assert not evidence.links[0].valid


def test_branch_start_cannot_override_explicit_dial_quest():
    content = fixture().replace(reference("SNAM", 0x10), reference("SNAM", 0x50))
    extra = record("DIAL", 0x50, reference("QNAM", 2))
    content += struct.pack("<4sI4sIHHHH", b"GRUP", len(extra) + 24, b"DIAL", 0, 0, 0, 0, 0) + extra
    evidence = read_dialogue_order_bytes(content)
    assert "DIALOGUE_ORDER_INVALID_BRANCH_START" in codes(evidence)
    assert not evidence.invalid_quests
    assert not next(item for item in evidence.records if item.kind == "DLBR").valid


def test_extended_size_header_counts_in_original_field_index():
    info = record("INFO", 0x11, field("XXXX", struct.pack("<I", 4)) + reference("TCLT", 0x20))
    content = plugin_bytes({
        "QUST": record("QUST", 1, b""),
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1))
        + children(0x10, info)
        + record("DIAL", 0x20, reference("QNAM", 1)),
    })
    evidence = read_dialogue_order_bytes(content)
    assert evidence.links[0].field_index == 1


@pytest.mark.parametrize(
    ("identities", "expected"),
    [
        (("NPC_",), "WRONG_REFERENCE_TYPE"),
        (("deleted",), "DELETED_REFERENCE"),
        (("NPC_", "NPC_"), "DUPLICATE_RECORD"),
        (("NPC_", "STAT", "deleted"), "DUPLICATE_RECORD"),
        ((), "MISSING_REFERENCE"),
    ],
)
def test_unrelated_record_identities_preserve_reference_failure_reason(identities, expected):
    unrelated = b""
    for kind in identities:
        item = record("NPC_" if kind == "deleted" else kind, 0x40, b"")
        if kind == "deleted":
            item = item[:8] + struct.pack("<I", 0x20) + item[12:]
        unrelated += item
    content = plugin_bytes({
        "QUST": record("QUST", 1, b""),
        "NPC_": unrelated,
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1))
        + children(0x10, record("INFO", 0x11, reference("TCLT", 0x40))),
    })
    evidence = read_dialogue_order_bytes(content)
    parsed = SSEPluginWithContext.from_stream(BytesIO(content), "plugin.esp")
    assert read_dialogue_order_evidence(parsed) == evidence
    assert codes(evidence) == {f"DIALOGUE_ORDER_{expected}"}
    assert evidence.invalid_quests == ("00000001",)
    assert not evidence.links[0].valid
    assert all(item.form_id != "00000040" for item in evidence.records)


@pytest.mark.parametrize("unrelated_first", [False, True])
def test_duplicate_identity_across_related_and_unrelated_kinds_invalidates_the_topic(unrelated_first):
    groups = {
        "QUST": record("QUST", 1, b""),
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1))
        + children(0x10, record("INFO", 0x11, reference("TCLT", 0x20)))
        + record("DIAL", 0x20, reference("QNAM", 1)),
    }
    unrelated = {"NPC_": record("NPC_", 0x20, b"") + record("NPC_", 0x20, b"")}
    groups = unrelated | groups if unrelated_first else groups | unrelated
    content = plugin_bytes(groups)
    evidence = read_dialogue_order_bytes(content)
    parsed = SSEPluginWithContext.from_stream(BytesIO(content), "plugin.esp")
    assert read_dialogue_order_evidence(parsed) == evidence
    assert codes(evidence) == {"DIALOGUE_ORDER_DUPLICATE_RECORD"}
    assert evidence.invalid_topics == ("00000010", "00000020")
    assert evidence.invalid_quests == ("00000001",)
    assert not next(item for item in evidence.records if item.form_id == "00000020").valid
    assert not evidence.links[0].valid


def test_skipped_unrelated_records_still_count_in_physical_ordinals():
    content = plugin_bytes({
        "NPC_": record("NPC_", 0x40, b"") + record("NPC_", 0x41, b""),
        "QUST": record("QUST", 1, b""),
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1))
        + children(
            0x10,
            record("NPC_", 0x42, b"") + record("INFO", 0x11, reference("TCLT", 0x20)) + record("NPC_", 0x43, b""),
        )
        + record("DIAL", 0x20, reference("QNAM", 1)),
    })
    evidence = read_dialogue_order_bytes(content)
    assert [(item.form_id, item.record_ordinal) for item in evidence.records] == [
        ("00000001", 3),
        ("00000010", 4),
        ("00000011", 6),
        ("00000020", 8),
    ]
    assert evidence.links[0].info_record_ordinal == 6
    assert evidence.links[0].valid
    parsed = SSEPluginWithContext.from_stream(BytesIO(content), "plugin.esp")
    assert read_dialogue_order_evidence(parsed) == evidence
    # Existing callers of the record iterators still receive every record.
    assert len(tuple(binary_records(content))) == 9
    assert len(tuple(parsed_records(parsed.groups))) == 8


@pytest.mark.parametrize("parsed_input", [False, True])
def test_cancellation_is_checked_while_skipping_unrelated_records(parsed_input):
    content = plugin_bytes({"NPC_": b"".join(record("NPC_", form_id, b"") for form_id in range(1, 100))})

    class Cancellation:
        checks = 0

        @property
        def is_cancelled(self):
            self.checks += 1
            return self.checks == 50

    cancellation = Cancellation()
    with pytest.raises(DialogueOrderCancelled):
        if parsed_input:
            parsed = SSEPluginWithContext.from_stream(BytesIO(content), "plugin.esp")
            read_dialogue_order_evidence(parsed, cancellation=cancellation)
        else:
            read_dialogue_order_bytes(content, cancellation=cancellation)
    assert cancellation.checks == 50


def test_old_artifact_uses_captured_bytes_once_without_opening_live_path(monkeypatch):
    snapshots._cache.clear()
    source = snapshot()
    original = snapshots.read_dialogue_order_bytes
    calls = []

    def counted(content, **kwargs):
        calls.append(content)
        return original(content, **kwargs)

    monkeypatch.setattr(snapshots, "read_dialogue_order_bytes", counted)
    monkeypatch.setattr(Path, "read_bytes", lambda _self: pytest.fail("Live plugin must never be opened"))
    evidence = snapshots.dialogue_order_from_snapshot(source)
    assert (
        snapshots.dialogue_order_from_snapshot(replace(source, source=SourceDescriptor("Y:/different.esp"))) is evidence
    )
    assert len(calls) == 1


def test_fresh_metadata_reuses_decoded_plugin_and_round_trips_without_scanning(monkeypatch):
    source = snapshot()
    plugin = SSEPluginWithContext.from_stream(BytesIO(source.content), "plugin.esp")
    pair = snapshots.capture_dialogue_order_metadata(plugin, source.sha256)
    source = replace(source, metadata=(pair,))
    monkeypatch.setattr(
        snapshots, "read_dialogue_order_bytes", lambda *_args, **_kwargs: pytest.fail("Unexpected rescan")
    )
    assert snapshots.dialogue_order_from_snapshot(source) == read_dialogue_order_evidence(plugin)


def test_stale_metadata_is_rejected_and_reported():
    source = snapshot(metadata=((snapshots.METADATA_KEY, (1, "0" * 64, ())),))
    evidence = snapshots.dialogue_order_from_snapshot(source)
    assert evidence.complete and evidence.links
    assert "DIALOGUE_ORDER_METADATA_INVALID" in codes(evidence)


def test_corrupt_binary_reports_incomplete_evidence_without_live_fallback():
    evidence = snapshots.dialogue_order_from_snapshot(snapshot(b"TES4"))
    assert not evidence.complete
    assert "DIALOGUE_ORDER_BINARY_INVALID" in codes(evidence)


def test_cancelled_extraction_propagates_even_with_cached_evidence():
    source = snapshot()
    snapshots.dialogue_order_from_snapshot(source)
    token = type("Cancelled", (), {"is_cancelled": True})()
    with pytest.raises(DialogueOrderCancelled):
        snapshots.dialogue_order_from_snapshot(source, cancellation=token)


def test_cancellation_during_old_byte_extraction_does_not_cache_partial_evidence():
    snapshots._cache.clear()

    class Cancellation:
        checks = 0

        @property
        def is_cancelled(self):
            self.checks += 1
            return self.checks > 5

    with pytest.raises(DialogueOrderCancelled):
        snapshots.dialogue_order_from_snapshot(snapshot(), cancellation=Cancellation())
    assert not snapshots._cache


def test_cache_is_bounded_and_different_captured_bytes_never_share_relations(monkeypatch):
    snapshots._cache.clear()
    monkeypatch.setattr(snapshots, "_CACHE_LIMIT", 1)
    first = snapshots.dialogue_order_from_snapshot(snapshot())
    second = snapshots.dialogue_order_from_snapshot(snapshot(fixture(target=0x999)))
    assert first.links[0].valid and not second.links[0].valid
    assert len(snapshots._cache) == 1
    assert all(type(key[0]) is str for key in snapshots._cache)


@pytest.mark.parametrize("invalid_payload", [(), (1,), ((), (), (), (), (), (), "complete")])
def test_corrupt_current_metadata_structure_is_rederived_with_a_diagnostic(invalid_payload):
    source = snapshot()
    source = replace(source, metadata=((snapshots.METADATA_KEY, (1, source.sha256, invalid_payload)),))
    evidence = snapshots.dialogue_order_from_snapshot(source)
    assert evidence.complete and evidence.links[0].valid
    assert "DIALOGUE_ORDER_METADATA_INVALID" in codes(evidence)


@pytest.mark.parametrize("corruption", ["missing_info", "duplicate_record", "wrong_parent", "wrong_kind"])
def test_contradictory_metadata_is_rejected_before_projection(corruption):
    source = snapshot()
    payload = list(evidence_payload(read_dialogue_order_bytes(source.content)))
    records = list(payload[0])
    if corruption == "missing_info":
        records = [record for record in records if record[0] != "INFO"]
    elif corruption == "duplicate_record":
        records.append(records[0])
    elif corruption == "wrong_kind":
        records[0] = ("NPC_", *records[0][1:])
    else:
        index = next(index for index, record in enumerate(records) if record[0] == "INFO")
        original = records[index]
        records[index] = (*original[:3], "00000020", *original[4:])
    payload[0] = tuple(records)
    source = replace(source, metadata=((snapshots.METADATA_KEY, (1, source.sha256, tuple(payload))),))
    evidence = snapshots.dialogue_order_from_snapshot(source)
    assert evidence.complete and evidence.links[0].valid
    assert "DIALOGUE_ORDER_METADATA_INVALID" in codes(evidence)


def test_nonplugin_snapshot_never_has_plugin_order_evidence():
    source = SourceSnapshot.from_bytes(SourceDescriptor("source.xml"), FormatId.XML_XT, b"<xml/>")
    assert snapshots.dialogue_order_from_snapshot(source) is None
