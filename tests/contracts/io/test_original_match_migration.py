from dataclasses import replace
import json
import struct
from types import SimpleNamespace

import pytest

from tests.plugin_fixtures import _field, write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import Provenance
from transbridge.application.io.migration_import import prepare_migration_import
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.xt import XT_Entry


def _entry(original):
    return TranslationEntry(
        "Same:00000800|1~NPC_:FULL",
        "Same:00000800|1~NPC_:FULL",
        original,
        "",
        0,
        "NPC_:FULL",
        requires_original_match=True,
    )


def test_internal_json_import_matches_original_without_changing_legacy_keys(tmp_path):
    first, second = _entry("First"), _entry("Second")
    collection = TranslationEntryCollection([first, second])
    source = tmp_path / "translations.json"
    source.write_text(
        json.dumps([
            replace(first, translation="第一条", stage=1).to_dict(),
            replace(second, translation="第二条", stage=3).to_dict(),
        ]),
        encoding="utf-8",
    )

    draft = prepare_migration_import(source, collection, format_hint=FormatId.JSON_TRANSBRIDGE)

    assert draft.state_mapping() == {first.identity: ("第一条", 1), second.identity: ("第二条", 3)}
    assert all(not entry.translation for entry in collection)


def test_translated_plugin_cannot_guess_flagged_targets_but_imports_ordinary(tmp_path, caplog):
    source = write_plugin(
        tmp_path / "source.esp", [(0x800, "Same", "First"), (0x800, "Same", "Second"), (0x801, "Other", "Normal")]
    )
    collection = TranslationEntryCollection(PluginParser().parse_plugin(source))
    translated = write_plugin(
        tmp_path / "translated.esp",
        [(0x800, "Same", "第一条"), (0x800, "Same", "第二条"), (0x801, "Other", "正常译文")],
    )

    draft = prepare_migration_import(translated, collection)

    normal = next(entry for entry in collection if not entry.requires_original_match)
    assert draft.state_mapping() == {normal.identity: ("正常译文", 1)}
    assert draft.skipped_conflicts == 2
    assert "SOURCE_ORIGINAL_MATCH_REQUIRED" in caplog.text


def test_paratranz_single_record_requires_exact_original_for_flagged_target(tmp_path, caplog):
    first, second = _entry("First"), _entry("Second")
    source = tmp_path / "translation.json"
    source.write_text(json.dumps([{"key": first.key, "original": "Second", "translation": "第二条"}]), encoding="utf-8")
    collection = TranslationEntryCollection([first, second])
    assert prepare_migration_import(source, collection).state_mapping() == {second.identity: ("第二条", 1)}
    source.write_text(json.dumps([{"key": first.key, "original": "Unknown", "translation": "错误"}]), encoding="utf-8")
    draft = prepare_migration_import(source, collection)
    assert not draft.states and draft.skipped_conflicts == 1
    assert "SOURCE_ORIGINAL_MATCH_REQUIRED" in caplog.text


def test_original_change_is_rejected_atomically_in_collection_mutation():
    first, second = _entry("First"), _entry("Second")
    collection = TranslationEntryCollection([first, second])
    changes = ChangeSet(
        "run",
        (EntryPatch.create(first.identity, translation="译文"), EntryPatch.create(second.identity, original="Changed")),
        ((first.identity, first.revision), (second.identity, second.revision)),
        Provenance("run", "test", "test"),
    )
    result = collection.apply(
        changes,
        RequestContext(
            "test", run_id="run", permissions=frozenset({"entry.translation.write", "entry.original.write"})
        ),
    )
    assert result.status is MutationStatus.CONFLICT
    assert result.diagnostics[0].code == "ENTRY_ORIGINAL_IDENTITY_IMMUTABLE"
    assert all(not entry.translation for entry in collection)


def test_fomod_write_back_keeps_same_key_translations_separate(tmp_path):
    from transbridge.fomod.pipeline import FomodPipeline
    from transbridge.fomod.plugin_publishing import prepare_plugin_source

    source = write_plugin(tmp_path / "source.esp", [(0x800, "Same", "First"), (0x800, "Same", "Second")])
    captured = prepare_plugin_source(source)
    entries = captured.collection_entries()
    collection = TranslationEntryCollection(
        replace(entry, translation=f"Translated {entry.original}", stage=1) for entry in entries
    )
    FomodPipeline.__new__(FomodPipeline)._write_back(source, collection, source=captured)
    assert [entry.original for entry in PluginParser().parse_plugin(source)] == [
        "Translated First",
        "Translated Second",
    ]


def test_dsd_migration_keeps_unique_originals_and_warns_for_indistinguishable_rows(tmp_path, caplog):
    from transbridge.converter.dsd_entries import parse_dsd_entries

    records = [
        {"form_id": "00000800|test.esp", "type": "QUST CNAM", "original": original, "string": translation}
        for original, translation in [("First", "T1"), ("First", "T1"), ("Second", "T2"), ("Second ", "T3")]
    ]
    source = tmp_path / "translations.dsd.json"
    source.write_text(json.dumps(records), encoding="utf-8")
    target = TranslationEntryCollection(replace(item, translation="", stage=0) for item in parse_dsd_entries(records))
    draft = prepare_migration_import(source, target, format_hint=FormatId.JSON_DSD)
    assert draft.state_mapping() == {item.identity: ("T2" if item.original == "Second" else "T3", 1) for item in target}
    assert draft.skipped_conflicts == 2
    assert "SOURCE_LOCATOR_CONFLICT" in caplog.text


def test_dsd_migration_all_indistinguishable_rows_is_warning_only(tmp_path):
    record = {"form_id": "00000800|test.esp", "type": "QUST CNAM", "original": "First", "string": "T1"}
    source = tmp_path / "translations.dsd.json"
    source.write_text(json.dumps([record, record]), encoding="utf-8")
    draft = prepare_migration_import(
        source, TranslationEntryCollection([_entry("First")]), format_hint=FormatId.JSON_DSD
    )
    assert not draft.states and draft.skipped_conflicts == 2


def test_indexed_dsd_export_migrates_back_to_real_plugin_with_colliding_legacy_keys(tmp_path):
    from tests.parser.test_original_match_plugin import quest_plugin

    source = quest_plugin(tmp_path / "quest.esp", ["First", "Second", "Second "])
    original_entries = PluginParser().parse_plugin(source)
    translated = TranslationEntryCollection(
        replace(item, translation=f"T{index}", stage=1) for index, item in enumerate(original_entries)
    )
    exported = tmp_path / "translations.json"
    translated.to_dsd_json_file(exported)
    records = json.loads(exported.read_text(encoding="utf-8"))
    assert [record["index"] for record in records] == [10, 11, 12]
    assert all("original" not in record for record in records)
    assert all(item.requires_original_match for item in original_entries)
    target = TranslationEntryCollection(original_entries)
    draft = prepare_migration_import(exported, target, format_hint=FormatId.JSON_DSD)
    assert draft.state_mapping() == {item.identity: (f"T{index}", 1) for index, item in enumerate(original_entries)}
    assert not draft.skipped_conflicts


def test_dsd_quest_matching_does_not_cross_plugin_or_guess_multiple_stages(tmp_path):
    from tests.parser.test_original_match_plugin import quest_plugin

    source = quest_plugin(tmp_path / "quest.esp", ["First", "Second"])
    first, second = PluginParser().parse_plugin(source)
    # Legacy DSD cannot distinguish repeated text across different stage locators.
    sibling = replace(first, key=first.key + "x", id=first.id + "x", entry_key=None)
    target = TranslationEntryCollection([first, second, sibling])
    exported = tmp_path / "translations.json"
    records = [
        {"form_id": "00000800|quest.esp", "type": "QUST CNAM", "original": first.original, "string": "T1"},
        {"form_id": "00000800|other.esp", "type": "QUST CNAM", "original": second.original, "string": "T2"},
    ]
    exported.write_text(json.dumps(records), encoding="utf-8")
    draft = prepare_migration_import(exported, target, format_hint=FormatId.JSON_DSD)
    assert not draft.states and draft.skipped_conflicts == 2


def test_legacy_dsd_quest_import_still_matches_exact_original_of_indexed_plugin(tmp_path):
    from tests.parser.test_original_match_plugin import quest_plugin

    plugin = quest_plugin(tmp_path / "quest.esp", ["First", "First "])
    first, second = PluginParser().parse_plugin(plugin)
    source = tmp_path / "legacy.json"
    source.write_text(
        json.dumps([
            {"form_id": "800|quest.esp", "type": "QUST CNAM", "original": "First ", "string": "Second"},
            {"form_id": "800|quest.esp", "type": "QUST CNAM", "original": " First", "string": "Wrong"},
        ]),
        encoding="utf-8",
    )

    draft = prepare_migration_import(source, TranslationEntryCollection([first, second]))

    assert draft.state_mapping() == {second.identity: ("Second", 1)}
    assert draft.skipped_conflicts == 1


@pytest.mark.parametrize("supplied_original", [None, "First"])
def test_indexed_dsd_quest_never_guesses_between_targets_with_same_index(tmp_path, supplied_original):
    from tests.parser.test_original_match_plugin import quest_plugin

    entry = PluginParser().parse_plugin(quest_plugin(tmp_path / "quest.esp", ["First"]))[0]
    sibling = replace(entry, key="sibling", id="sibling", entry_key=None, original="Second")
    source = tmp_path / "modern.json"
    record = {"form_id": "800|quest.esp", "type": "QUST CNAM", "index": 10, "string": "Wrong"}
    if supplied_original is not None:
        record["original"] = supplied_original
    source.write_text(json.dumps([record]), encoding="utf-8")

    draft = prepare_migration_import(source, TranslationEntryCollection([entry, sibling]))

    assert not draft.states and draft.skipped_conflicts == 1


@pytest.mark.parametrize("source_fields", [{"index": 99, "original": "First"}, {"index": 10, "original": "Wrong"}])
def test_indexed_dsd_quest_does_not_fall_back_to_legacy_matching(tmp_path, source_fields):
    from tests.parser.test_original_match_plugin import quest_plugin

    entries = PluginParser().parse_plugin(quest_plugin(tmp_path / "quest.esp", ["First"]))
    source = tmp_path / "modern.json"
    record = {"form_id": "800|quest.esp", "type": "QUST CNAM", "string": "Wrong", **source_fields}
    source.write_text(json.dumps([record]), encoding="utf-8")

    draft = prepare_migration_import(source, TranslationEntryCollection(entries))

    assert not draft.states and draft.skipped_conflicts == 1


def test_indexed_dsd_quest_strict_matching_requires_supplied_original(tmp_path):
    from tests.parser.test_original_match_plugin import quest_plugin

    entries = PluginParser().parse_plugin(quest_plugin(tmp_path / "quest.esp", ["First"]))
    source = tmp_path / "modern.json"
    record = {"form_id": "800|quest.esp", "type": "QUST CNAM", "string": "Translated", "index": 10}
    source.write_text(json.dumps([record]), encoding="utf-8")
    target = TranslationEntryCollection(entries)

    missing_original = prepare_migration_import(source, target, require_original_match=True)
    assert not missing_original.states and missing_original.skipped_conflicts == 1

    source.write_text(json.dumps([dict(record, original="First")]), encoding="utf-8")
    matched_original = prepare_migration_import(source, target, require_original_match=True)
    assert matched_original.state_mapping() == {entries[0].identity: ("Translated", 1)}


def test_indexed_dsd_quest_migrates_the_exact_condition_variant(tmp_path):
    from transbridge.converter.dsd_entries import parse_dsd_entries

    base = {"form_id": "00000800|quest.esp", "type": "QUST CNAM", "index": 0}
    conditions = ["GetIsID(0x800~Quest.esp) == 1.0", "GetLevel() >= 10.0"]
    records = [dict(base, string="Plain"), dict(base, string="Conditional", conditions=conditions)]
    target = TranslationEntryCollection(replace(entry, translation="", stage=0) for entry in parse_dsd_entries(records))
    source = tmp_path / "modern.json"
    source.write_text(
        json.dumps([*records, dict(base, string="Unexpected", conditions=list(reversed(conditions)))]),
        encoding="utf-8",
    )

    draft = prepare_migration_import(source, target)

    plain, conditional = target
    assert draft.state_mapping() == {plain.identity: ("Plain", 1), conditional.identity: ("Conditional", 1)}
    assert draft.skipped_conflicts == 1


def test_dsd_condition_cannot_overwrite_nonquest_unconditional_target(tmp_path):
    from transbridge.converter.dsd_entries import parse_dsd_entries

    base = {"form_id": "00000800|npc.esp", "type": "NPC_ FULL"}
    target = TranslationEntryCollection(parse_dsd_entries([dict(base, string="")]))
    source = tmp_path / "modern.json"
    source.write_text(
        json.dumps([dict(base, string="Plain"), dict(base, string="Conditional", conditions=["GetLevel() >= 10.0"])]),
        encoding="utf-8",
    )

    draft = prepare_migration_import(source, target)

    assert draft.state_mapping() == {next(iter(target)).identity: ("Plain", 1)}
    assert draft.skipped_unmatched == 1


def test_legacy_dsd_quest_prefers_native_original_only_target(tmp_path):
    from transbridge.converter.dsd_entries import parse_dsd_entries

    record = {"form_id": "800|quest.esp", "type": "QUST CNAM", "original": "First", "string": "Legacy"}
    native = parse_dsd_entries([record, dict(record, index=1, string="Indexed")])
    target = TranslationEntryCollection(replace(entry, translation="", stage=0) for entry in native)
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps([record]), encoding="utf-8")

    draft = prepare_migration_import(source, target)
    assert draft.state_mapping() == {native[0].identity: ("Legacy", 1)}

    cross_version = prepare_migration_import(source, TranslationEntryCollection([native[1]]))
    assert cross_version.state_mapping() == {native[1].identity: ("Legacy", 1)}


@pytest.mark.parametrize("index", [None, 0, 1])
def test_dsd_region_optional_index_migrates_to_unique_proven_record(tmp_path, index):
    from tests.parser.test_plugin_dsd_indices import _parse, _raw_field

    position = 1 if index == 1 else 0
    _, entries = _parse(
        tmp_path,
        b"REGN",
        _raw_field(b"RDAT", struct.pack("<IBB2x", 2, 0, 0)) * position
        + _raw_field(b"RDAT", struct.pack("<IBB2x", 4, 0, 0))
        + _field(b"RDMP", "Map name"),
    )
    assert entries[0].dsd_index == position
    source = tmp_path / "region.json"
    record = {"form_id": "800|source.esp", "type": "REGN RDMP", "string": "Map translation"}
    if index is not None:
        record["index"] = index
    source.write_text(json.dumps([record]), encoding="utf-8")

    draft = prepare_migration_import(source, TranslationEntryCollection(entries))

    assert draft.state_mapping() == {entries[0].identity: ("Map translation", 1)}


@pytest.mark.parametrize("format_name", ["eet", "xt", "sst"])
def test_legacy_import_original_matching_is_exact_and_does_not_take_first_sibling(format_name):
    from transbridge.converter.translation_import_matching import match_eet_updates, match_xt_updates

    entries = [_entry("Same"), _entry(" Same ")]
    if format_name == "eet":
        source = [
            SimpleNamespace(
                edid="Same",
                id="00000800",
                index=1,
                grup="NPC_",
                champ="FULL",
                original=entry.original,
                traduit=f"translation {index}",
            )
            for index, entry in enumerate(entries)
        ]
        result = list(match_eet_updates(entries, source))
    elif format_name == "xt":
        source = [
            XT_Entry(
                list_id=0, edid="Same", rec="NPC_:FULL", source=entry.original, dest=f"translation {index}", index=1
            )
            for index, entry in enumerate(entries)
        ]
        result = list(match_xt_updates(entries, source))
    else:
        source = [
            SimpleNamespace(
                rec="NPC_FULL", form_id=0x800, index=1, text=entry.original, translated_text=f"translation {index}"
            )
            for index, entry in enumerate(entries)
        ]
        collection = TranslationEntryCollection(entries)
        collection.apply_sst_entries(source)
        result = list(collection)
    assert [(entry.original, entry.translation) for entry in result] == [
        ("Same", "translation 0"),
        (" Same ", "translation 1"),
    ]
