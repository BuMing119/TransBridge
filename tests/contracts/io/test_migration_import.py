from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import struct

import pytest

from transbridge.application.io import EntryKey, FormatId, SourceNamespace
from transbridge.application.io.migration_import import MigrationImportError, prepare_migration_import
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt.sst_parser import SST_Parser


def _collection(*entries: TranslationEntry) -> TranslationEntryCollection:
    return TranslationEntryCollection(entries)


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
