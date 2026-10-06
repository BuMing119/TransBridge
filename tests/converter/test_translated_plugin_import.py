from dataclasses import replace
from pathlib import Path

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser


def _collection(path: Path) -> TranslationEntryCollection:
    return TranslationEntryCollection(PluginParser().parse_plugin(path))


def test_import_uses_translated_plugin_text_and_preserves_source_identity(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello")])
    translated = write_plugin(tmp_path / "translated.esp", [(0x800, "TestNpc", "你好")])
    collection = _collection(source)
    original = next(iter(collection)).snapshot()

    assert collection.update_from_translated_plugin(translated) == 1

    entry = next(iter(collection))
    assert entry.original == "Hello"
    assert entry.translation == "你好"
    assert entry.stage == 1
    assert entry.identity == original.entry_key
    assert collection.update_from_translated_plugin(translated, overwrite=True) == 0


@pytest.mark.parametrize("text", ["Hello", "", "   "])
def test_unchanged_or_empty_plugin_text_is_skipped(tmp_path, text):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello")])
    translated = write_plugin(tmp_path / "translated.esp", [(0x800, "TestNpc", text)])
    collection = _collection(source)
    before = next(iter(collection)).snapshot()

    assert collection.update_from_translated_plugin(translated) == 0
    assert next(iter(collection)).snapshot() == before


@pytest.mark.parametrize("overwrite, expected, count", [(False, "人工译文", 0), (True, "你好", 1)])
def test_existing_translation_obeys_overwrite(tmp_path, overwrite, expected, count):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello")])
    translated = write_plugin(tmp_path / "translated.esp", [(0x800, "TestNpc", "你好")])
    collection = TranslationEntryCollection(
        replace(entry, translation="人工译文", stage=3) for entry in _collection(source)
    )

    assert collection.update_from_translated_plugin(translated, overwrite=overwrite) == count
    entry = next(iter(collection))
    assert entry.translation == expected
    assert entry.stage == (1 if overwrite else 3)


def test_unmatched_plugin_records_do_not_fill_other_entries(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello"), (0x801, "OtherNpc", "你好")])
    translated = write_plugin(tmp_path / "translated.esp", [(0x802, "UnrelatedNpc", "你好")])
    collection = _collection(source)
    before = [entry.snapshot() for entry in collection]

    assert collection.update_from_translated_plugin(translated) == 0
    assert [entry.snapshot() for entry in collection] == before


def test_conflicting_plugin_ids_skip_unproved_originals_and_import_normal_entries(tmp_path, caplog):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello"), (0x801, "OtherNpc", "Goodbye")])
    translated = write_plugin(
        tmp_path / "translated.esp",
        [(0x800, "TestNpc", "你好"), (0x801, "OtherNpc", "再见"), (0x801, "OtherNpc", "告辞")],
    )
    collection = _collection(source)
    assert collection.update_from_translated_plugin(translated) == 1
    assert [entry.translation for entry in collection] == ["你好", ""]
    assert "SOURCE_ORIGINAL_MATCH_REQUIRED" in caplog.text


def test_ambiguous_target_ids_reject_before_any_update(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "TestNpc", "Hello"), (0x801, "OtherNpc", "Goodbye")])
    translated = write_plugin(tmp_path / "translated.esp", [(0x800, "TestNpc", "你好"), (0x801, "OtherNpc", "再见")])
    entries = list(_collection(source))
    duplicate = replace(entries[1], entry_key=EntryKey(SourceNamespace("other-source"), entries[1].key))
    collection = TranslationEntryCollection([*entries, duplicate])
    before = [entry.snapshot() for entry in collection]

    with pytest.raises(ValueError, match="多个目标词条"):
        collection.update_from_translated_plugin(translated)

    assert [entry.snapshot() for entry in collection] == before
