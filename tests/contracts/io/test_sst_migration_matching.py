from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.io import FormatId
from transbridge.application.io.migration_import import prepare_migration_import
from transbridge.converter.sst_matching import apply_entries, target_locator, update_entry
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt.sst_codec import SstDocument, SstRecord
from transbridge.parser.xt.sst_parser import SST_Entry


def _target(kind, quest):
    return TranslationEntry.create_from_plugin_entry(
        SimpleNamespace(
            type=kind,
            editor_id="Dialogue",
            form_id="00000800|Example.esp",
            index=1,
            string="Original line",
            context=SimpleNamespace(quest=quest),
        )
    )


def _source(kind, original="Original line"):
    record_type, field_type = kind.split()
    return SstRecord(
        list_index=2 if record_type == "INFO" else 0,
        string_id=0,
        form_id=0x800,
        record_type=record_type,
        field_type=field_type,
        index=0,
        index_max=0,
        edid_hash=0,
        collab_id=0,
        params=1,
        original=original,
        translation="Translated line",
    )


@pytest.mark.parametrize("kind", ["INFO NAM1", "DIAL FULL"])
@pytest.mark.parametrize("quest", ["", "00012345|Example.esp"])
@pytest.mark.parametrize("legacy_context_only", [False, True])
def test_dialogue_migration_uses_physical_field_not_display_context(kind, quest, legacy_context_only):
    target = _target(kind, quest)
    assert "|" in target.context
    if legacy_context_only:
        target = replace(target, dsd_type="")
    assert target_locator(target) == (0x800, kind.replace(" ", ""))
    source = SST_Entry.from_record(_source(kind), 0)
    migrated = update_entry(target, source)
    assert migrated is not None
    assert migrated.translation == "Translated line"
    assert migrated.identity == target.identity
    assert migrated.context == target.context


@pytest.mark.parametrize("kind", ["INFO NAM1", "DIAL FULL"])
def test_application_dialogue_migration_requires_exact_original(tmp_path, kind):
    target = _target(kind, "00012345|Example.esp")
    source = tmp_path / "dialogue.sst"
    document = SstDocument(b"SSU9", 0, ("example.esp",), (), (_source(kind),))
    source.write_bytes(document.to_bytes())
    collection = TranslationEntryCollection([target])
    draft = prepare_migration_import(source, collection, format_hint=FormatId.SST_SSU9)
    assert draft.state_mapping() == {target.identity: ("Translated line", 1)}
    assert not next(iter(collection)).translation
    source.write_bytes(replace(document, records=(_source(kind, "Different original"),)).to_bytes())
    draft = prepare_migration_import(source, collection, format_hint=FormatId.SST_SSU9)
    assert not draft.states
    assert draft.skipped_conflicts == 1


def test_dialogue_matching_does_not_relax_original_or_field_constraints():
    target = _target("INFO NAM1", "00012345|Example.esp")
    assert update_entry(target, SST_Entry.from_record(_source("INFO NAM1", "Different original"), 0)) is None
    assert update_entry(target, SST_Entry.from_record(_source("DIAL FULL"), 0)) is None


def test_indistinguishable_dialogue_records_are_not_guessed():
    target = _target("INFO NAM1", "00012345|Example.esp")
    collection = TranslationEntryCollection([target])
    source = SST_Entry.from_record(_source("INFO NAM1"), 0)
    assert apply_entries(collection, (source, replace(source, ordinal=1))) == {"matched": 0, "updated": 0, "skipped": 0}
    assert not next(iter(collection)).translation


@pytest.mark.parametrize("second_translation", ["Translated line", ""])
def test_application_skips_duplicate_source_records_even_when_translation_agrees(tmp_path, second_translation):
    target = _target("INFO NAM1", "")
    record = _source("INFO NAM1")
    document = SstDocument(
        b"SSU9", 0, ("example.esp",), (), (record, replace(record, index=1, translation=second_translation))
    )
    source = tmp_path / "ambiguous.sst"
    source.write_bytes(document.to_bytes())
    collection = TranslationEntryCollection([target])
    draft = prepare_migration_import(source, collection)
    assert not draft.states
    assert draft.skipped_conflicts == (2 if second_translation else 1)
    assert not next(iter(collection)).translation
