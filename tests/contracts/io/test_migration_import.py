from __future__ import annotations

from collections import Counter
import json
import logging
from pathlib import Path
import struct

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.io import EntryKey, FormatId, SourceNamespace
from transbridge.application.io.legacy_migration import prepare_legacy_migration
from transbridge.application.io.migration_import import MigrationImportError, prepare_migration_import
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.xt.sst_parser import SST_Parser


def _collection(*entries: TranslationEntry) -> TranslationEntryCollection:
    return TranslationEntryCollection(entries)


@pytest.mark.parametrize("suffix", ["strings", "dlstrings", "ilstrings"])
def test_strings_draft_preserves_id_matching_and_existing_translation(tmp_path, suffix):
    source_text = "导入译文".encode() + b"\0"
    data = source_text if suffix == "strings" else struct.pack("<I", len(source_text)) + source_text
    (tmp_path / f"Plugin_Chinese.{suffix}").write_bytes(struct.pack("<IIII", 1, len(data), 7, 0) + data)
    target = _collection(
        TranslationEntry("empty", "empty", "Source", "", 0, "NPC_:FULL", string_id=7),
        TranslationEntry("protected", "protected", "Other", "人工译文", 3, "INFO:NAM1", string_id=7),
        TranslationEntry("unmatched", "unmatched", "Other", "", 0, "NPC_:FULL", string_id=8),
    )
    before = tuple(entry.snapshot() for entry in target)

    draft = prepare_legacy_migration(target, strings_dir=str(tmp_path), plugin_stem="Plugin", strings_lang="chinese")

    assert draft.collection.get("empty").translation == "导入译文"
    assert draft.collection.get("protected").translation == "人工译文"
    assert draft.collection.get("protected").stage == 3
    assert not draft.collection.get("unmatched").translation
    assert tuple(entry.snapshot() for entry in target) == before
    assert draft.strings_lookup.get(7) == "导入译文"


def test_strings_rejects_partial_file_instead_of_importing_valid_prefix(tmp_path):
    text = b"Good\0"
    (tmp_path / "Plugin_Chinese.strings").write_bytes(struct.pack("<IIIIII", 2, len(text), 7, 0, 8, 1000) + text)
    target = _collection(TranslationEntry("one", "one", "Source", "", 0, None, string_id=7))

    with pytest.raises(MigrationImportError, match="MIGRATION_STRINGS_INVALID"):
        prepare_legacy_migration(target, strings_dir=str(tmp_path), plugin_stem="Plugin", strings_lang="chinese")

    assert not target.get("one").translation


def test_paratranz_draft_maps_only_an_existing_unique_local_key(tmp_path: Path) -> None:
    target = _collection(
        TranslationEntry(
            id="Editor:00000001|1~INFO:NAM1",
            key="local-key",
            original="Hello",
            translation="",
            stage=0,
            context="INFO:NAM1",
        ),
        TranslationEntry(id="other", key="other", original="Other", translation="", stage=0, context=None),
    )
    source = tmp_path / "paratranz.json"
    source.write_text(
        json.dumps([{"id": 17, "key": "local-key", "original": "Hello", "translation": "你好", "stage": 1}]),
        encoding="utf-8",
    )

    draft = prepare_migration_import(source, target)

    assert draft.format_id is FormatId.JSON_PARATRANZ
    assert draft.state_mapping() == {target.get("local-key").identity: ("你好", 1)}


def test_ambiguous_empty_json_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = _collection(TranslationEntry(id="one", key="one", original="One", translation="", stage=0, context=None))
    before = tuple(entry.snapshot() for entry in target)
    source = tmp_path / "empty.json"
    source.write_text("[]", encoding="utf-8")

    with pytest.raises(MigrationImportError, match="MIGRATION_FORMAT_AMBIGUOUS"):
        prepare_migration_import(source, target)

    assert tuple(entry.snapshot() for entry in target) == before


def test_ssu8_draft_uses_form_id_and_index_without_mutating_target() -> None:
    source = Path("tests/trans_exe/xt/ssu8/ccbgssse010-petdwarvenarmoredmudcrab_english_chinese.sst")
    sst_entries = SST_Parser.from_file(str(source)).entries
    counts = Counter((entry.form_id, entry.index) for entry in sst_entries)
    sst_entry = next(entry for entry in sst_entries if counts[(entry.form_id, entry.index)] == 1)
    target_entry = TranslationEntry(
        id=f"Mudcrab:{sst_entry.form_id:08X}|{sst_entry.index}~NPC_:FULL",
        key="mudcrab",
        original=sst_entry.text,
        translation="",
        stage=0,
        context="NPC_:FULL",
    )
    target = _collection(target_entry)

    draft = prepare_migration_import(source, target, format_hint=FormatId.SST_SSU8)

    assert draft.format_id is FormatId.SST_SSU8
    assert draft.state_mapping() == {target_entry.identity: (sst_entry.translated_text, 1)}
    assert target_entry.translation == ""


def test_mapping_ambiguity_rejects_the_whole_draft(tmp_path: Path) -> None:
    target = _collection(
        TranslationEntry(
            id="first",
            key="same",
            original="One",
            translation="",
            stage=0,
            context=None,
            entry_key=EntryKey(SourceNamespace("source:first"), "same"),
        ),
        TranslationEntry(
            id="second",
            key="same",
            original="Two",
            translation="",
            stage=0,
            context=None,
            entry_key=EntryKey(SourceNamespace("source:second"), "same"),
        ),
    )
    source = tmp_path / "paratranz.json"
    source.write_text(
        json.dumps([{"id": 1, "key": "same", "original": "Text", "translation": "译文", "stage": 1}]),
        encoding="utf-8",
    )

    with pytest.raises(MigrationImportError, match="MIGRATION_MAPPING_AMBIGUOUS"):
        prepare_migration_import(source, target)

    assert all(not entry.translation for entry in target)


def test_ssu8_without_translated_text_has_stable_diagnostic(tmp_path: Path) -> None:
    original = "Original".encode("utf-16-le")
    source = tmp_path / "untranslated.sst"
    source.write_bytes(
        b"SSU8"
        + (b"\0" * 12)
        + struct.pack("<H8sII2sI", 0x0500, b"NPC_FULL", 0, 1, b"\x01\0", len(original))
        + original
        + struct.pack("<I", 0)
        + b"\0"
        + struct.pack("<IH", 1, 1)
    )
    target = _collection(
        TranslationEntry(
            id="NPC_:00000001|1~NPC_:FULL",
            key="npc",
            original="Original",
            translation="",
            stage=0,
            context="NPC_:FULL",
        )
    )

    with pytest.raises(MigrationImportError, match="MIGRATION_SST_NO_TRANSLATIONS"):
        prepare_migration_import(source, target)

    assert not target.get("npc").translation


def test_translated_plugin_draft_uses_text_without_mutating_target(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello")])
    target = TranslationEntryCollection(PluginParser().parse_plugin(source))
    translated = write_plugin(tmp_path / "translated.esp", [(0x800, "TestNpc", "你好")])
    entry = next(iter(target))

    draft = prepare_migration_import(translated, target, format_hint=FormatId.PLUGIN_SSE)

    assert draft.format_id is FormatId.PLUGIN_SSE
    assert draft.state_mapping() == {entry.identity: ("你好", 1)}
    assert next(iter(target)).translation == ""


def test_unchanged_plugin_text_is_not_marked_translated(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello")])
    target = TranslationEntryCollection(PluginParser().parse_plugin(source))

    draft = prepare_migration_import(source, target)

    assert not draft.states
    assert next(iter(target)).stage == 0


@pytest.mark.parametrize("all_conflicted", [False, True])
def test_plugin_skips_conflicting_record_ids_with_warning(tmp_path, caplog, all_conflicted):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello"), (0x801, "OtherNpc", "Other")])
    target = TranslationEntryCollection(PluginParser().parse_plugin(source))
    translated = write_plugin(
        tmp_path / "translated.esp",
        ([] if all_conflicted else [(0x800, "TestNpc", "你好")])
        + [(0x801, "OtherNpc", "同文"), (0x801, "OtherNpc", "同文")],
    )

    with caplog.at_level(logging.WARNING):
        draft = prepare_migration_import(translated, target)

    expected = {} if all_conflicted else {next(iter(target)).identity: ("你好", 1)}
    assert draft.state_mapping() == expected
    assert draft.skipped_conflicts == 2
    assert draft.skipped_unmatched == 0
    warnings = [record for record in caplog.records if "SOURCE_LOCATOR_CONFLICT" in record.message]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert str(translated) in warnings[0].message
    assert "2 条" in warnings[0].message
    assert all(not entry.translation for entry in target)


def test_unmatched_plugin_records_do_not_use_text_fallback(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello")])
    target = TranslationEntryCollection(PluginParser().parse_plugin(source))
    translated = write_plugin(tmp_path / "translated.esp", [(0x801, "OtherNpc", "你好")])

    with pytest.raises(MigrationImportError, match="MIGRATION_NO_PROVABLE_MATCHES"):
        prepare_migration_import(translated, target)

    assert next(iter(target)).translation == ""
