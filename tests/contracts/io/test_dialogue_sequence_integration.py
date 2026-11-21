"""Real plugin bytes exercise shared reading and ParaTranz wire projections."""

from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from transbridge.application.categorized_upload import UploadSource, categorize_sources
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.application.file_upload import wire_records
from transbridge.application.io.dialogue_reading_view import build_dialogue_reading_view
from transbridge.application.io.paratranz_classification import classify_entries, classify_selection
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.application.io.paratranz_keys import split_ordered_key
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from transbridge.parser.plugin.dialogue_order_evidence import read_dialogue_order_evidence
from transbridge.parser.plugin_parser import PluginParser


def _response(number, text):
    return field("TRDT", bytes(12) + bytes([number]) + bytes(11)) + field("NAM1", text.encode() + b"\0")


def _children(topic, content):
    return struct.pack("<4sIIiHHHH", b"GRUP", len(content) + 24, topic, 7, 0, 0, 0, 0) + content


def _topic(topic, text, info):
    return record("DIAL", topic, reference("QNAM", 1) + field("FULL", text.encode() + b"\0")) + _children(topic, info)


def _scene(form_id, topics_in_phase_order):
    return record(
        "SCEN",
        form_id,
        b"".join(
            field("ANAM", b"\0\0")
            + reference("SNAM", phase)
            + reference("ENAM", phase)
            + reference("DATA", topic)
            + field("ANAM", b"")
            for phase, topic in reversed(tuple(enumerate(topics_in_phase_order)))
        )
        + reference("PNAM", 1),
    )


@pytest.fixture
def sequence_source():
    # Neither numeric FormIDs nor DIAL physical order match the scene actions.
    content = plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"Quest\0") + field("FULL", b"Sequence Quest\0")),
        "DIAL": (
            _topic(0x80, "First orphan", record("INFO", 0x81, _response(1, "First orphan reply")))
            + _topic(
                0x70,
                "Second orphan",
                record("INFO", 0x71, _response(1, "Second orphan reply") + reference("TCLT", 0x80)),
            )
            + _topic(0x20, "Second scene topic", record("INFO", 0x21, _response(1, "Second scene reply")))
            + _topic(0x30, "Shared topic", record("INFO", 0x31, _response(1, "Shared reply")))
            + _topic(
                0x90,
                "Default question",
                record(
                    "INFO",
                    0x91,
                    _response(2, "Alternate first response")
                    + _response(1, "Alternate second response")
                    + field("RNAM", b"Alternate question\0"),
                )
                + record("INFO", 0x92, _response(1, "Default response")),
            )
        ),
        "SCEN": _scene(0xF0, (0x90, 0x30)) + _scene(0xA0, (0x30, 0x20)),
    })
    parser = PluginParser()
    entries = tuple(
        parser.parse_plugin(
            Path("sequence.esp"),
            captured_content=content,
            discover_sibling_strings=False,
            skip_empty=False,
            raise_on_error=True,
        )
    )
    plugin = parser.get_plugin()
    return entries, plugin, read_dialogue_order_evidence(plugin)


def _dialogue_group(groups):
    return next(group for group in groups if group.category_id == "dialogue:00000001")


EXPECTED = [
    "Default question",
    "Default response",
    "Alternate question",
    "Alternate first response",
    "Alternate second response",
    "Shared topic",
    "Shared reply",
    "Second scene topic",
    "Second scene reply",
    "Second orphan",
    "Second orphan reply",
    "First orphan",
    "First orphan reply",
]


def test_real_bytes_share_prompt_scene_and_orphan_order_without_mutation(sequence_source):
    entries, plugin, evidence = sequence_source
    before = tuple(entry.to_dict() for entry in entries)
    view = build_dialogue_reading_view(entries, evidence)
    by_key = {entry.identity: entry for entry in entries}
    visible = [by_key[key].original for key in view.ordered_keys if "|00000001" in (by_key[key].context or "")]
    group = _dialogue_group(classify_entries(entries, dialogue_order=evidence))
    assert visible == EXPECTED
    assert [entry.original for entry in group.entries] == EXPECTED
    assert len({entry.identity for entry in group.entries}) == len(EXPECTED)
    assert view.topic_orders[entries[0].identity.namespace, "00000001"] == (
        "00000090",
        "00000030",
        "00000020",
        "00000070",
        "00000080",
    )

    index = DialogueIndexLoader().build(entries, plugin=plugin)
    quest = next(quest for quest in index.quests if quest.identity[1] == "00000001")
    scenes = [topic for topic in quest.topics if topic.kind == "SCEN"]
    assert [topic.identity[2] for topic in scenes] == ["SCEN:000000F0", "SCEN:000000A0"]
    assert [by_key[key].original for key in scenes[0].entries] == EXPECTED[:7]
    assert [by_key[key].original for key in scenes[1].entries] == EXPECTED[5:9]
    default = next(topic for topic in quest.topics if topic.identity[2] == "00000090")
    assert [by_key[key].original for key in default.entries] == EXPECTED[:5]
    assert tuple(entry.to_dict() for entry in entries) == before


def test_invalid_topic_links_leave_verified_scenes_and_questions_available(sequence_source):
    entries, _, evidence = sequence_source
    graph_invalid = replace(evidence, invalid_quests=("00000001",))
    group = _dialogue_group(classify_entries(entries, dialogue_order=graph_invalid))
    assert [entry.original for entry in group.entries] == [
        *EXPECTED[:-4],
        "First orphan",
        "First orphan reply",
        "Second orphan",
        "Second orphan reply",
    ]


def test_unlocated_entry_keeps_its_quest_slot_without_suppressing_verified_questions(sequence_source):
    entries, _, evidence = sequence_source
    damaged = replace(evidence, records=tuple(r for r in evidence.records if r.form_id != "00000081"))
    group = _dialogue_group(classify_entries(entries, dialogue_order=damaged))
    texts = [entry.original for entry in group.entries]
    assert texts[1] == "First orphan reply"
    assert [text for text in texts if text in EXPECTED[:5]] == EXPECTED[:5]


def test_partial_classification_and_tb2_wire_keep_complete_source_ranks(sequence_source, tmp_path):
    entries, _, evidence = sequence_source
    complete = _dialogue_group(classify_entries(entries, dialogue_order=evidence))
    selected_originals = {"Default response", "Alternate question", "Alternate second response", "Second scene reply"}
    selected = tuple(entry for entry in reversed(entries) if entry.original in selected_originals)
    subset = _dialogue_group(classify_selection(selected, entries, dialogue_order=evidence))
    expected = tuple(entry for entry in complete.entries if entry.original in selected_originals)
    assert subset.entries == expected
    assert [entry.original for entry in subset.entries] == [text for text in EXPECTED if text in selected_originals]
    files = categorize_sources((
        UploadSource("source", "sequence.esp", selected, evidence, classification_entries=entries),
    ))
    file = next(file for file in files if file.category_id == "dialogue:00000001")
    assert file.entries == expected
    wire = wire_records(file)
    assert [split_ordered_key(item["key"])[0] for item in wire] == [entry.key for entry in expected]
    ranks = [split_ordered_key(item["key"])[1] for item in wire]
    assert ranks == [parse_ordered_context(entry.context)[1] for entry in expected]
    assert ranks == sorted(ranks)
    assert ranks[-1] - ranks[0] > len(ranks) - 1
    assert tuple(sorted(wire, key=lambda item: item["key"])) == wire
    export_to_categorized_json_files(
        TranslationEntryCollection(selected),
        tmp_path,
        dialogue_order=evidence,
        classification_entries=entries,
    )
    local = json.loads((tmp_path / subset.path).read_text(encoding="utf-8"))
    assert local == list(wire)
