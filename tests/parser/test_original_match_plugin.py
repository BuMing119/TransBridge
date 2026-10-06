"""Real plugin coverage for text-disambiguated quest log entries."""

from dataclasses import replace
import gc
import struct
import weakref

import pytest

from tests.plugin_fixtures import _field, _record
from transbridge.application.contracts import DiagnosticSeverity, OperationOutcome, RequestContext
from transbridge.application.io import (
    EntryKey,
    FormatId,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    WriteRequest,
)
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.writer.plugin_writer import PluginWriter


def quest_plugin(path, logs):
    stage = b"INDX" + struct.pack("<HHBB", 4, 10, 0, 0)
    data = b"".join(b"QSDT\x01\x00\x00" + _field(b"CNAM", text) for text in logs)
    quest = _record(b"QUST", 0x800, _field(b"EDID", "Quest") + stage + data)
    group = struct.pack("<4sI4siHHI", b"GRUP", 24 + len(quest), b"QUST", 0, 0, 0, 0) + quest
    path.write_bytes(_record(b"TES4", 0, b"") + group)
    return path


def physical_texts(plugin):
    return [ps.string for ps, _ in plugin.extract_string_pairs_with_context()]


def test_distinct_logs_keep_public_keys_and_write_crossed_translations(tmp_path):
    source = quest_plugin(tmp_path / "quest.esp", ["First", "Second", "Third", "Fourth"])
    original = source.read_bytes()
    parser = PluginParser()
    entries = parser.parse_plugin(source)
    assert len(entries) == 4
    assert len({entry.key for entry in entries}) == 1
    assert all(entry.id == entry.key and entry.requires_original_match for entry in entries)
    assert len({entry.identity for entry in entries}) == 4
    assert parser.conflict_skipped_count == 0
    translations = ["Second", "First", "Fourth", "Third"]
    collection = TranslationEntryCollection([
        replace(entry, translation=text, stage=1) for entry, text in zip(entries, translations, strict=True)
    ])
    writer = PluginWriter(parser.get_plugin())
    assert writer.apply_collection(collection) == 4
    assert physical_texts(parser.get_plugin()) == translations
    output = tmp_path / "out.esp"
    writer.write(output)
    reparsed = PluginParser()
    assert [entry.original for entry in reparsed.parse_plugin(output)] == translations
    assert source.read_bytes() == original


@pytest.mark.parametrize("logs,retained,skipped", [(["A", "A", "B"], ["B"], 2), (["A", "A"], [], 2)])
def test_identical_logs_are_all_skipped_before_collection(tmp_path, caplog, logs, retained, skipped):
    parser = PluginParser()
    entries = parser.parse_plugin(quest_plugin(tmp_path / "quest.esp", logs))
    assert [entry.original for entry in entries] == retained
    assert all(entry.requires_original_match for entry in entries)
    assert parser.conflict_skipped_count == skipped
    assert len(parser.conflict_diagnostics) == skipped
    assert all(item.severity is DiagnosticSeverity.WARNING for item in parser.conflict_diagnostics)
    assert "SOURCE_LOCATOR_CONFLICT" in caplog.text
    writer = PluginWriter(parser.get_plugin())
    assert writer.apply_collection(
        TranslationEntryCollection([replace(entry, translation="Translated", stage=1) for entry in entries])
    ) == len(retained)
    assert physical_texts(parser.get_plugin()) == ["Translated" if text == "B" else text for text in logs]
    ps = parser.get_plugin().extract_strings_with_context()[0]
    assert parser.get_plugin().find_string_subrecord(ps.form_id, ps.type, ps.string, ps.index) is None


def test_original_match_is_exact_and_missing_original_does_not_write(tmp_path):
    parser = PluginParser()
    logs = ["A", " A", "A\n", "A\r\n"]
    entries = parser.parse_plugin(quest_plugin(tmp_path / "quest.esp", logs))
    assert [entry.original for entry in entries] == logs
    wrong = replace(entries[0], original="absent", entry_key=None, translation="Changed", stage=1)
    writer = PluginWriter(parser.get_plugin())
    assert writer.apply_collection(TranslationEntryCollection([wrong])) == 0
    assert physical_texts(parser.get_plugin()) == logs


def test_plain_request_cannot_write_ambiguous_source(tmp_path):
    parser = PluginParser()
    entries = parser.parse_plugin(quest_plugin(tmp_path / "quest.esp", ["A", "B"]))
    plain = replace(entries[0], requires_original_match=False, entry_key=None, translation="Changed", stage=1)
    assert PluginWriter(parser.get_plugin()).apply_collection(TranslationEntryCollection([plain])) == 0
    assert physical_texts(parser.get_plugin()) == ["A", "B"]


@pytest.mark.parametrize("forgery", ["plain", "original", "string_id"])
def test_snapshot_write_rejects_changed_discriminator(tmp_path, forgery):
    source = quest_plugin(tmp_path / "quest.esp", ["A", "B"])
    adapter = SsePluginAdapter()
    context = RequestContext("test")
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE))
    entry = parsed.entries[0]
    if forgery == "plain":
        changed = replace(entry, requires_original_match=False, entry_key=EntryKey(entry.identity.namespace, entry.key))
    elif forgery == "original":
        changed = replace(entry, original="absent", entry_key=EntryKey(entry.identity.namespace, entry.key, "absent"))
    else:
        changed = replace(entry, string_id=99)
    changed = replace(changed, translation="Changed", stage=1)
    target = tmp_path / "output.esp"
    result = adapter.write(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            (changed,),
            1,
            context,
            source_snapshot=parsed.source_snapshot,
        )
    )
    assert result.outcome is OperationOutcome.FAILED
    assert not target.exists()


def test_localized_colliding_logs_write_distinct_string_ids(tmp_path):
    source = tmp_path / "localized.esp"
    stage = b"INDX" + struct.pack("<HHBB", 4, 10, 0, 0)
    logs = b"".join(b"QSDT\x01\x00\x00" + b"CNAM\x04\x00" + struct.pack("<I", index) for index in (1, 2))
    quest = _record(b"QUST", 0x800, _field(b"EDID", "Quest") + stage + logs)
    header = bytearray(_record(b"TES4", 0, b""))
    header[8:12] = struct.pack("<I", 0x80)
    source.write_bytes(header + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(quest), b"QUST", 0, 0, 0, 0) + quest)
    strings = tmp_path / "Strings"
    strings.mkdir()
    payload = b"\x02\x00\x00\x00A\x00\x02\x00\x00\x00B\x00"
    (strings / "localized_English.dlstrings").write_bytes(struct.pack("<IIIIII", 2, len(payload), 1, 0, 2, 6) + payload)
    adapter = SsePluginAdapter()
    context = RequestContext("test")
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE))
    assert len(parsed.entries) == 2
    assert all(entry.requires_original_match for entry in parsed.entries)
    assert [entry.string_id for entry in parsed.entries] == [1, 2]
    entries = tuple(
        replace(entry, translation=text, stage=1) for entry, text in zip(parsed.entries, ["B", "A"], strict=True)
    )
    target = tmp_path / "output.esp"
    result = adapter.write(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            entries,
            1,
            context,
            source_snapshot=parsed.source_snapshot,
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED
    parser = PluginParser()
    assert [entry.original for entry in parser.parse_plugin(target)] == ["B", "A"]


def test_empty_sibling_still_requires_original_match(tmp_path):
    parser = PluginParser()
    entries = parser.parse_plugin(quest_plugin(tmp_path / "quest.esp", ["", "A"]))
    assert len(entries) == 1 and entries[0].requires_original_match
    writer = PluginWriter(parser.get_plugin())
    assert (
        writer.apply_collection(TranslationEntryCollection([replace(entries[0], translation="Changed", stage=1)])) == 1
    )
    assert physical_texts(parser.get_plugin()) == ["", "Changed"]


@pytest.mark.parametrize("reuse_writer", [True, False])
def test_repeated_writes_keep_original_locators_and_can_restore_original(tmp_path, reuse_writer):
    parser = PluginParser()
    entries = parser.parse_plugin(quest_plugin(tmp_path / "quest.esp", ["A", "B"]))
    plugin = parser.get_plugin()
    writer = PluginWriter(plugin)
    first = TranslationEntryCollection([
        replace(entry, translation=f"First {entry.original}", stage=1) for entry in entries
    ])
    assert writer.apply_collection(first) == 2
    if not reuse_writer:
        writer = PluginWriter(plugin)
    second = TranslationEntryCollection([
        replace(entry, translation=f"Second {entry.original}", stage=1) for entry in entries
    ])
    assert writer.apply_collection(second) == 2
    assert physical_texts(plugin) == ["Second A", "Second B"]
    restored = TranslationEntryCollection([replace(entry, translation=entry.original, stage=1) for entry in entries])
    assert writer.apply_collection(restored) == 2
    assert physical_texts(plugin) == ["A", "B"]
    assert writer.apply_collection(second) == 2
    assert writer.apply_collection(TranslationEntryCollection(entries)) == 2
    assert physical_texts(plugin) == ["A", "B"]


def test_changed_lookup_keeps_inline_originals_and_cache_does_not_keep_plugin_alive(tmp_path):
    parser = PluginParser()
    entries = parser.parse_plugin(quest_plugin(tmp_path / "quest.esp", ["A", "B"]))
    plugin = parser.get_plugin()
    first = TranslationEntryCollection([
        replace(entry, translation=f"First {entry.original}", stage=1) for entry in entries
    ])
    assert PluginWriter(plugin).apply_collection(first) == 2
    second = TranslationEntryCollection([
        replace(entry, translation=f"Second {entry.original}", stage=1) for entry in entries
    ])
    assert PluginWriter(plugin, strings_lookup=PluginStringsLookup({})).apply_collection(second) == 2
    assert physical_texts(plugin) == ["Second A", "Second B"]
    reference = weakref.ref(plugin)
    del parser, plugin
    gc.collect()
    assert reference() is None
