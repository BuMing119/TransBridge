# See LICENSE and NOTICE for TransBridge licensing and attribution terms.

from dataclasses import replace
import json

import pytest

from transbridge.application.io.json_source import parse_json_source
from transbridge.converter.dsd_entries import parse_dsd_entries
from transbridge.converter.dsd_metadata import (
    DSD_QUEST_INDEX,
    DSD_REGION_INDEX,
    dsd_conditions,
    dsd_has_replacement,
    dsd_quest_index,
)
from transbridge.converter.translation_entry import STAGE_TRANSLATED, STAGE_UNTRANSLATED, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def test_dsd_condition_variants_keep_identity_and_order_through_file_and_internal_json(tmp_path):
    basic = {"form_id": "0x0176cD|Skyrim.esm", "type": "FLOR FULL", "string": "Salmon"}
    when_raining = ["Self IsInInterior NONE NONE == 0 AND", "Self IsRaining NONE NONE == 1"]
    records = [
        {**basic, "conditions": when_raining},
        {**basic, "form_id": "0x0176cD|Other.esp", "conditions": when_raining},
        {**basic, "conditions": when_raining[::-1]},
        {**basic, "conditions": []},
        basic,
    ]
    source = tmp_path / "input.json"
    source.write_text(json.dumps(records), encoding="utf-8")
    public = TranslationEntryCollection.from_dsd_json_file(source)
    adapter = parse_json_source(str(source), {"format": "dsd"}).collection
    stored = json.loads(public.to_json())
    restored = TranslationEntryCollection(TranslationEntry.from_dict(entry) for entry in stored)
    for collection in (public, adapter, restored):
        assert len({entry.identity for entry in collection}) == len(records)
        assert [dsd_conditions(entry) for entry in collection] == [
            tuple(when_raining),
            tuple(when_raining),
            tuple(when_raining[::-1]),
            (),
            None,
        ]
        output = tmp_path / "output.json"
        collection.to_dsd_json_file(output)
        assert json.loads(output.read_text(encoding="utf-8")) == records


def test_native_dsd_quest_selectors_and_other_optional_fields_roundtrip():
    quest = {"form_id": "00000800|Quest.esp", "type": "QUST CNAM", "string": "Translated log"}
    original = "Exact original\r\ntext "
    records = [
        {**quest, "index": 0},
        {**quest, "index": 1},
        {**quest, "index": 1, "original": original},
        {**quest, "original": original},
        {"form_id": "556|Weather.esp", "type": "REGN RDMP", "index": 0, "editor_id": "MyRegion", "string": "Map"},
        {"form_id": "0X5|Test.esp", "type": "INFO NAM1", "editor_id": "", "string": "No index supplied"},
        {"form_id": "2|Test.esp", "type": "NPC_ FULL", "original": "", "string": "Explicitly empty original"},
    ]
    parsed = parse_dsd_entries(records)
    assert [dsd_quest_index(entry) for entry in parsed[:4]] == [0, 1, 1, None]
    assert [entry.original for entry in parsed[:4]] == ["", "", original, original]
    assert len({entry.identity for entry in parsed}) == len(records)
    assert TranslationEntryCollection(parsed).to_export_dict() == records


def test_generated_dsd_uses_proven_indices_and_legacy_quest_original():
    quest = TranslationEntry("quest", "quest", "Original", "Translation", 1, "QUST:CNAM", dsd_type="QUST CNAM")
    assert quest.to_dsd_dict()["original"] == "Original"
    assert "index" not in quest.to_dsd_dict()
    at_zero = replace(quest, metadata=((DSD_QUEST_INDEX, 0),), dsd_index=0).to_dsd_dict()
    assert at_zero["index"] == 0
    assert "original" not in at_zero
    with pytest.raises(ValueError, match="请从原插件重新提取词条"):
        replace(quest, original="").to_dsd_dict()

    region = replace(quest, dsd_type="REGN RDMP")
    assert "index" not in region.to_dsd_dict()
    assert replace(region, metadata=((DSD_REGION_INDEX, 1),)).to_dsd_dict()["index"] == 1


def test_native_dsd_preserves_an_explicit_empty_replacement_only():
    raw_empty = {"form_id": "0x176CD|Skyrim.esm", "type": "FLOR RNAM", "string": ""}
    raw_missing = {"form_id": "0x16837|Skyrim.esm", "type": "FLOR RNAM"}
    raw_nonempty = {**raw_empty, "form_id": "0x133C6|Skyrim.esm", "string": "Pull"}
    empty, missing, nonempty = parse_dsd_entries([raw_empty, raw_missing, raw_nonempty])
    assert empty.stage == STAGE_TRANSLATED
    assert missing.stage == STAGE_UNTRANSLATED
    assert dsd_has_replacement(empty)
    assert not dsd_has_replacement(missing)
    assert not dsd_has_replacement(replace(empty, stage=STAGE_UNTRANSLATED))
    assert not dsd_has_replacement(replace(nonempty, translation=""))
    stored = json.loads(TranslationEntryCollection([empty, missing, nonempty]).to_json())
    restored = TranslationEntryCollection(TranslationEntry.from_dict(entry) for entry in stored)
    assert restored.to_export_dict() == [raw_empty, raw_nonempty]


@pytest.mark.parametrize("index", [-1, 0x100000000])
def test_native_dsd_rejects_out_of_range_indices(index):
    with pytest.raises(ValueError, match="DSD index"):
        TranslationEntry.from_dsd_dict({"form_id": "4|Quest.esp", "type": "QUST CNAM", "index": index, "string": "Log"})


@pytest.mark.parametrize("field", ["form_id", "type", "string", "editor_id", "original"])
def test_native_dsd_rejects_null_text_fields(field):
    record = {"form_id": "4|Quest.esp", "type": "QUST CNAM", "index": 0, "string": "Log", field: None}
    with pytest.raises(TypeError, match=f"DSD {field} must be a string"):
        TranslationEntry.from_dsd_dict(record)


def test_dsd_methods_preserve_entry_subclass():
    class CustomEntry(TranslationEntry):
        DSD_INDEX_TYPES = frozenset({"REGN FULL"})

    record = {"form_id": "4|Weather.esp", "type": "REGN FULL", "string": "Region"}
    assert type(CustomEntry.from_dsd_dict(record)) is CustomEntry
    entry = CustomEntry("region", "region", "", "Region", 1, "REGN:FULL", dsd_type="REGN FULL", dsd_index=0)
    assert entry.to_dsd_dict()["index"] == 0
