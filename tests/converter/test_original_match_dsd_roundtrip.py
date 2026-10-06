from dataclasses import replace
import json

from transbridge.converter.dsd_entries import parse_dsd_entries
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools._json_import import parse_json_source


def record(original, translation="Translated", *, form_id="00000800|quest.esp"):
    return {"form_id": form_id, "type": "QUST CNAM", "original": original, "string": translation}


def test_dsd_file_and_assistant_roundtrip_preserve_colliding_originals(tmp_path):
    entries = [
        TranslationEntry(
            "Quest:00000800|101~QUST:CNAM",
            "Quest:00000800|101~QUST:CNAM",
            original,
            translation,
            1,
            "QUST:CNAM",
            form_id_with_plugin="00000800|quest.esp",
            dsd_type="QUST CNAM",
            requires_original_match=True,
        )
        for original, translation in [("A", "First"), ("B", "Second")]
    ]
    source = TranslationEntryCollection(entries)
    path = tmp_path / "export.json"
    source.to_dsd_json_file(path)
    public = TranslationEntryCollection.from_dsd_json_file(path)
    assistant, _, _, _ = parse_json_source(str(path), {"format": "dsd"})
    for loaded in (public, assistant):
        assert len(loaded) == 2
        assert [(entry.original, entry.translation) for entry in loaded] == [("A", "First"), ("B", "Second")]
        assert all(entry.requires_original_match for entry in loaded)
        assert len({entry.key for entry in loaded}) == 1
        assert len({entry.identity for entry in loaded}) == 2
        assert loaded.to_export_dict() == source.to_export_dict()


def test_dsd_exact_duplicates_are_all_skipped_and_plain_entries_stay_plain(tmp_path, caplog):
    records = [record("A"), record("A", "Conflicting"), record("B"), record("Normal", form_id="00000801|quest.esp")]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    loaded = TranslationEntryCollection.from_dsd_json_file(path)
    assert [entry.original for entry in loaded] == ["B", "Normal"]
    assert [entry.requires_original_match for entry in loaded] == [True, False]
    assert "SOURCE_LOCATOR_CONFLICT" in caplog.text
    expected = [TranslationEntry.from_dsd_dict(records[index]) for index in (2, 3)]
    assert [(entry.key, entry.id) for entry in loaded] == [(entry.key, entry.id) for entry in expected]


def test_dsd_original_comparison_preserves_whitespace():
    originals = ["A", " A", "A\n", "A\r\n"]
    loaded = parse_dsd_entries(record(text) for text in originals)
    assert [entry.original for entry in loaded] == originals
    assert len(TranslationEntryCollection(loaded)) == 4
    assert [replace(entry, translation="X").identity for entry in loaded] == [entry.identity for entry in loaded]
