from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.dialogue_catalog_support import (
    dialogue_plugin,
    dialogue_plugin_bytes,
    field,
    plugin_bytes,
    record,
    reference,
)
from tests.dialogue_support import dialogue_entries
from transbridge.application.dialogue import loading
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.dialogue_reading_view import ReadingEntry
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.parser.plugin_parser import PluginParser


def snapshot(scene_name="TestScene"):
    return SourceSnapshot.from_bytes(
        SourceDescriptor("nonexistent/fixture.esp"), FormatId.PLUGIN_SSE, dialogue_plugin_bytes(scene_name)
    )


@pytest.mark.parametrize("source_kind", ["plugin", "snapshot"])
def test_source_catalog_is_cached_across_translation_changes_and_replaced_on_source_change(monkeypatch, source_kind):
    calls = []
    read = loading.read_dialogue_catalog

    def counted(plugin):
        calls.append(plugin)
        return read(plugin)

    monkeypatch.setattr(loading, "read_dialogue_catalog", counted)
    loader = loading.DialogueIndexLoader()
    source = dialogue_plugin() if source_kind == "plugin" else snapshot()
    first = loader.build(dialogue_entries(), **{source_kind: source})
    changed = [replace(entry, translation="新译文") for entry in dialogue_entries()]
    assert loader.build(changed, **{source_kind: source}) == first
    assert len(calls) == 1
    loader.build(changed, **{source_kind: dialogue_plugin() if source_kind == "plugin" else snapshot("ChangedScene")})
    assert len(calls) == 2
    assert all(topic.kind != "SCEN" for quest in loader.build(changed).quests for topic in quest.topics)


def test_snapshot_hydration_reads_captured_bytes_without_opening_the_source_path():
    entries = [replace(entry, form_id_with_plugin=None, editor_id="") for entry in dialogue_entries()]
    result = loading.DialogueIndexLoader().build(entries, snapshot=snapshot())
    assert result.quests[0].label == "QUST {Quest} [00000001]"
    assert any(topic.label == "SCEN {TestScene} [00000012]" for topic in result.quests[0].topics)


@pytest.mark.parametrize("source_kind", ["plugin", "snapshot"])
def test_entry_relationships_are_delivered_before_reading_a_new_source_catalog(monkeypatch, source_kind):
    previews = []
    read = loading.read_dialogue_catalog
    entries = dialogue_entries()

    def read_after_preview(plugin):
        assert len(previews) == 1
        assert set(previews[0].locations) == {entry.identity for entry in entries}
        assert all(topic.kind != "SCEN" for quest in previews[0].quests for topic in quest.topics)
        return read(plugin)

    monkeypatch.setattr(loading, "read_dialogue_catalog", read_after_preview)
    loader = loading.DialogueIndexLoader()
    source = dialogue_plugin() if source_kind == "plugin" else snapshot()
    result = loader.build(iter(entries), on_preview=previews.append, **{source_kind: source})
    assert set(result.locations) == set(previews[0].locations)
    assert any(topic.kind == "SCEN" for quest in result.quests for topic in quest.topics)
    assert loader.build(entries, on_preview=previews.append, **{source_kind: source}) == result
    assert len(previews) == 1


def test_broken_snapshot_reports_parser_failure_and_allows_a_later_valid_source():
    source = SourceSnapshot.from_bytes(SourceDescriptor("broken.esp"), FormatId.PLUGIN_SSE, b"invalid plugin")
    loader = loading.DialogueIndexLoader()
    with pytest.raises(Exception):
        loader.build(dialogue_entries(), snapshot=source)
    assert loader.build(dialogue_entries(), snapshot=snapshot()).quests


def reading_source():
    def children(topic, content):
        return struct.pack("<4sIIiHHHH", b"GRUP", len(content) + 24, topic, 7, 0, 0, 0, 0) + content

    def response(number, text):
        return field("TRDT", bytes(12) + bytes([number]) + bytes(11)) + field("NAM1", text.encode() + b"\0")

    content = plugin_bytes({
        "QUST": record("QUST", 1, field("FULL", b"Journal\0")),
        "DIAL": (
            record("DIAL", 0x10, reference("QNAM", 1) + field("FULL", b"Later\0"))
            + children(0x10, record("INFO", 0x11, response(1, "Later response")))
            + record("DIAL", 0x20, reference("QNAM", 1))
            + children(0x20, record("INFO", 0x21, reference("TCLT", 0x10)))
            + record("DIAL", 0x30, reference("QNAM", 1))
            + children(
                0x30, record("INFO", 0x31, response(2, "First") + response(1, "Second") + reference("TCLT", 0x20))
            )
        ),
    })
    parser = PluginParser()
    entries = tuple(
        parser.parse_plugin(
            Path("absent.esp"),
            captured_content=content,
            skip_empty=False,
            discover_sibling_strings=False,
            raise_on_error=True,
        )
    )
    source = SourceSnapshot.from_bytes(SourceDescriptor("absent.esp"), FormatId.PLUGIN_SSE, content)
    return entries, source, parser.get_plugin()


@pytest.mark.parametrize("source_kind", ["plugin", "snapshot"])
def test_loader_applies_shared_plugin_layout_with_empty_bridge_without_entry_mutation(monkeypatch, source_kind):
    entries, source, plugin = reading_source()
    before = [entry.to_dict() for entry in entries]

    def deny_open(*_args, **_kwargs):
        pytest.fail("Dialogue loading must not reopen the source or an XML reference")

    monkeypatch.setattr(Path, "open", deny_open)
    index = loading.DialogueIndexLoader().build(
        entries, **{source_kind: source if source_kind == "snapshot" else plugin}
    )
    quest = index.quests[0]
    assert [topic.identity[2] for topic in quest.topics] == ["journal", "00000030", "00000020", "00000010"]
    assert quest.topics[2].entries == ()
    by_identity = {entry.identity: entry for entry in entries}
    assert [by_identity[key].original for key in quest.topics[1].entries] == ["First", "Second"]
    assert [entry.to_dict() for entry in entries] == before


def test_loader_reuses_captured_evidence_when_only_translation_changes(monkeypatch):
    entries, source, _plugin = reading_source()
    calls = []
    read = loading.dialogue_order_from_snapshot

    def counted(snapshot, **kwargs):
        calls.append(snapshot.sha256)
        return read(snapshot, **kwargs)

    monkeypatch.setattr(loading, "dialogue_order_from_snapshot", counted)
    loader = loading.DialogueIndexLoader()
    initial = loader.build(entries, snapshot=source)
    changed = tuple(replace(entry, translation="new translation", stage=3) for entry in entries)
    assert loader.build(changed, snapshot=source) == initial
    assert calls == [source.sha256]


def test_loader_checks_cancellation_before_source_or_catalog_work(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A cancelled task must not read the source")

    monkeypatch.setattr(loading, "read_dialogue_catalog", forbidden)
    with pytest.raises(DialogueOrderCancelled):
        loading.DialogueIndexLoader().build(
            dialogue_entries(), snapshot=snapshot(), cancellation=SimpleNamespace(is_cancelled=True)
        )


def test_tree_uses_complete_captured_reading_scope_when_current_entries_omit_a_topic():
    entries, source, _plugin = reading_source()
    complete = tuple(ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries)
    current = tuple(entry for entry in entries if entry.original not in {"Later", "Later response"})
    index = loading.DialogueIndexLoader().build(current, snapshot=source, reading_entries=complete)
    assert [topic.identity[2] for topic in index.quests[0].topics] == ["journal", "00000030", "00000020", "00000010"]
    assert index.quests[0].topics[-1].entries == ()
    assert set(index.locations) == {entry.identity for entry in current}


def test_tree_reuses_completed_workbench_reading_layout_for_same_frozen_source(monkeypatch):
    from transbridge.application.io import dialogue_reading_view as reading

    entries, source, _plugin = reading_source()
    complete = tuple(ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries)
    evidence = loading.dialogue_order_from_snapshot(source)
    view = reading.build_dialogue_reading_view(complete, evidence)

    def unexpected_layout(*_args, **_kwargs):
        pytest.fail("A completed full-source layout should be shared with the tree")

    monkeypatch.setattr(reading, "_build_reading_view", unexpected_layout)
    index = loading.DialogueIndexLoader().build(entries, snapshot=source, reading_entries=complete)
    topics = tuple(topic.identity[2] for topic in index.quests[0].topics if topic.identity[2] != "journal")
    assert topics == view.topic_orders[entries[0].identity.namespace, "00000001"]
    assert set(index.locations) == {entry.identity for entry in entries}
