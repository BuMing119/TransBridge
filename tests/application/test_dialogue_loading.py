from dataclasses import replace
from pathlib import Path
import struct
import sys
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
from transbridge.application.io.plugin_dialogue_order import capture_dialogue_order_metadata
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.parser.plugin.text_subrecords import PluginTextEncodingError
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


def encoded_snapshot(payload, *, encoding=None, metadata=()):
    content = plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"Quest\0") + field("FULL", payload + b"\0")),
        "DIAL": record("DIAL", 0x10, field("EDID", b"Topic10\0") + reference("QNAM", 1)),
    })
    return SourceSnapshot.from_bytes(
        SourceDescriptor("nonexistent/encoded.esp"), FormatId.PLUGIN_SSE, content, encoding=encoding, metadata=metadata
    )


@pytest.fixture
def catalog_texts(monkeypatch):
    observed = []
    read = loading.read_dialogue_catalog

    def observe(plugin):
        observed.append(tuple(str(item.string) for item in plugin.inline_text_fields()))
        return read(plugin)

    def forbid_open(*_args, **_kwargs):
        pytest.fail("Directory reconstruction must only read captured bytes")

    monkeypatch.setattr(loading, "read_dialogue_catalog", observe)
    monkeypatch.setattr(Path, "open", forbid_open)
    return observed


@pytest.mark.parametrize(
    ("payload", "encoding", "metadata", "expected"),
    [
        ("武器".encode("cp936"), "cp936", (("source_language", "chinese"), ("text_read_policy", "strict-v1")), "武器"),
        (b"Anna\xefg", "utf-8", (("source_language", "russian"), ("text_read_policy", "xtranslator-v1")), "Annaпg"),
        (b"Anna\xefg", None, (), "Annaďg"),
        (b"Anna\xefg", "cp1252", (), "Annaïg"),
    ],
)
def test_snapshot_catalog_replays_saved_text_facts(catalog_texts, payload, encoding, metadata, expected):
    source = encoded_snapshot(payload, encoding=encoding, metadata=metadata)
    before_source = (source.content, source.encoding, source.metadata)
    entries = dialogue_entries()
    before_entries = [entry.to_dict() for entry in entries]

    index = loading.DialogueIndexLoader().build(entries, snapshot=source)

    assert index.quests
    assert catalog_texts == [(expected,)]
    assert (source.content, source.encoding, source.metadata) == before_source
    assert [entry.to_dict() for entry in entries] == before_entries


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native v2 decoding")
def test_same_bytes_with_changed_saved_policy_rebuild_catalog(catalog_texts):
    source = encoded_snapshot(
        b"\x81", encoding="utf-8", metadata=(("source_language", "english"), ("text_read_policy", "xtranslator-v1"))
    )
    changed = replace(source, metadata=(("source_language", "english"), ("text_read_policy", "xtranslator-v2")))
    entries = dialogue_entries()
    loader = loading.DialogueIndexLoader()
    initial = loader.build(entries, snapshot=source)
    translated = [replace(entry, translation="New translation", stage=3) for entry in entries]
    assert loader.build(translated, snapshot=source) == initial
    assert catalog_texts == [("Ѓ",)]

    assert changed.content == source.content
    assert loader.build(entries, snapshot=changed).quests

    assert catalog_texts == [("Ѓ",), ("\x81",)]


@pytest.mark.parametrize("metadata", [(), (("text_read_policy", "strict-v1"),)])
def test_strict_snapshot_errors_do_not_install_cache_and_can_be_retried(catalog_texts, metadata):
    loader = loading.DialogueIndexLoader()
    entries = dialogue_entries()
    valid = encoded_snapshot(b"Valid")
    initial = loader.build(entries, snapshot=valid)
    invalid = encoded_snapshot(b"\xe9", encoding="utf-8", metadata=metadata)

    for _ in range(2):
        with pytest.raises(PluginTextEncodingError, match=r"encoded\.esp QUST FULL") as error:
            loader.build(entries, snapshot=invalid)
        assert isinstance(error.value.__cause__, UnicodeDecodeError)
    assert loader.build(entries, snapshot=valid) == initial
    assert catalog_texts == [("Valid",)]

    corrected = replace(invalid, encoding="cp1252")
    assert loader.build(entries, snapshot=corrected).quests
    assert catalog_texts == [("Valid",), ("é",)]


@pytest.mark.parametrize(
    ("encoding", "metadata"),
    [
        ("invalid-codec", ()),
        ("utf-8", (("text_read_policy", "invalid-policy"),)),
        ("utf-8", (("source_language", "invalid-language"),)),
    ],
)
def test_invalid_snapshot_read_facts_are_reported_without_falling_back(catalog_texts, encoding, metadata):
    source = encoded_snapshot(b"Valid", encoding=encoding, metadata=metadata)

    with pytest.raises(ValueError):
        loading.DialogueIndexLoader().build(dialogue_entries(), snapshot=source)

    assert catalog_texts == []


def test_snapshot_without_bytes_reuses_live_plugin_without_new_parse(monkeypatch, catalog_texts):
    plugin = dialogue_plugin()
    source = replace(snapshot(), content=None, encoding="invalid-codec", lease_id="metadata-only-test")

    def forbid_parse(*_args, **_kwargs):
        pytest.fail("An already parsed plugin must be reused")

    monkeypatch.setattr(loading.SSEPluginWithContext, "from_stream", forbid_parse)

    assert loading.DialogueIndexLoader().build(dialogue_entries(), plugin=plugin, snapshot=source).quests
    assert len(catalog_texts) == 1


def test_paged_legacy_snapshot_restores_stringless_topics_and_order_without_plugin(monkeypatch):
    entries, captured, plugin = reading_source()
    descriptor = replace(captured, content=None, lease_id="project-translation-content")
    saved = replace(descriptor, metadata=(capture_dialogue_order_metadata(plugin, captured.sha256),))
    reads = []

    def load_fields():
        reads.append(True)
        return saved

    def forbid_parse(*args, **kwargs):
        pytest.fail("An offline legacy project must use its saved relationships")

    monkeypatch.setattr(loading.SSEPluginWithContext, "from_stream", forbid_parse)
    loader = loading.DialogueIndexLoader()
    index = loader.build(entries, snapshot=descriptor, load_snapshot=load_fields)
    assert [topic.identity[2] for topic in index.quests[0].topics] == ["journal", "00000030", "00000020", "00000010"]
    assert index.quests[0].topics[2].entries == ()
    assert loader.build(entries, snapshot=descriptor, load_snapshot=load_fields) == index
    assert reads == [True]

    # A different plugin with no evidence must not inherit the previous source's directory.
    empty = replace(snapshot("Other"), content=None, lease_id="project-translation-content")
    rebuilt = loader.build(entries, snapshot=empty, load_snapshot=lambda: empty)
    assert all(topic.identity[2] != "00000020" for quest in rebuilt.quests for topic in quest.topics)
