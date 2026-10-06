from dataclasses import replace

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.migrator.key_migrator import migrate, plan_migration
from transbridge.translation_memory.contracts import TranslationMemoryQuery
from transbridge.translation_memory.manager import QueryContext, TranslationMemoryManager
from transbridge.translation_memory.service import TranslationMemoryQueryService


def entry(original, translation="", *, key="same", flagged=True):
    return TranslationEntry(
        key, key, original, translation, 1 if translation else 0, "", requires_original_match=flagged
    )


@pytest.mark.parametrize("originals", [("First", "Second"), ("First", "First "), ("A\nB", "A\r\nB")])
def test_dictionary_roundtrip_retains_exact_originals_and_both_apply_paths(tmp_path, originals):
    rows = [entry(originals[0], "T1"), entry(originals[1], "T2")]
    manager = TranslationMemoryManager(tmp_path)
    assert (
        manager.save_from_collection(
            TranslationEntryCollection(rows),
            mod_file_id="mod",
            source_locale="en_US",
            target_locale="zh_CN",
            source_namespace="legacy:v1",
            source_fingerprint="fingerprint",
        )
        == 2
    )
    manager.save()
    restored = TranslationMemoryManager(tmp_path)
    restored.load()
    dictionary = restored.snapshot_dictionaries()[0]
    assert len(dictionary.entries) == 2
    assert not dictionary.text_index  # Qualified records cannot pollute normalized fallback.
    targets = TranslationEntryCollection(replace(row, translation="", stage=0) for row in rows)
    assert restored.apply_to_collection(targets, QueryContext("mod")).applied == 2
    assert [row.translation for row in targets] == ["T1", "T2"]
    for row in rows:
        result = TranslationMemoryQueryService(restored).query(
            TranslationMemoryQuery(row.identity, row.original, "en_US", "zh_CN", 0, "fingerprint")
        )
        assert result.selected.translation == row.translation
        assert not result.requires_confirmation


@pytest.mark.parametrize("preferred", ["mod", "other"])
def test_legacy_dictionary_cannot_fill_other_original_or_key(preferred, caplog):
    manager = TranslationMemoryManager()
    manager.add("same", "First", "T1", mod_file_id="mod")
    targets = TranslationEntryCollection([
        entry("First"),
        entry("Second"),
        entry("First "),
        entry("First", key="other"),
    ])
    result = manager.apply_to_collection(targets, QueryContext(preferred))
    assert result.applied == 1 and result.misses == 3
    assert [row.translation for row in targets] == ["T1", "", "", ""]
    assert "SOURCE_ORIGINAL_MATCH_REQUIRED" in caplog.text


def test_v2_dictionary_does_not_accept_normalized_or_text_fallback():
    manager = TranslationMemoryManager()
    manager.add(
        "same",
        "First",
        "T1",
        mod_file_id="mod",
        source_locale="en_US",
        target_locale="zh_CN",
        source_namespace="legacy:v1",
        source_fingerprint="fingerprint",
    )
    for row in [entry("First "), entry("First", key="other")]:
        result = TranslationMemoryQueryService(manager).query(
            TranslationMemoryQuery(row.identity, row.original, "en_US", "zh_CN", 0, "fingerprint")
        )
        assert not result.candidates and result.selected is None


def test_qualified_dictionary_records_do_not_replace_normal_dictionary_records():
    manager = TranslationMemoryManager()
    normal = entry("First", "normal", flagged=False)
    qualified = entry("First", "qualified")
    manager.save_from_collection(TranslationEntryCollection([normal, qualified]), mod_file_id="mod")
    assert manager.query(normal.key, normal.original).translation == "normal"
    assert manager.query(qualified.identity.serialize(), qualified.original).translation == "qualified"


def test_dictionary_conflict_receipt_identifies_only_the_qualified_target():
    manager = TranslationMemoryManager()
    first, second = entry("First"), entry("Second")
    for name, text in [("one", "T1"), ("two", "T2")]:
        manager.add(first.identity.serialize(), first.original, text, mod_file_id=name)
    collection = TranslationEntryCollection([first, second])
    result = manager.apply_to_collection(collection)
    assert len(result.conflicts) == 1
    assert collection.get(result.conflicts[0]["entry_id"]) is first
    assert not second.translation


def test_migration_matches_each_original_independently():
    old = [entry("First", "T1"), entry("Second", "T2")]
    new = [entry("First"), entry("Second")]
    planned = plan_migration(old, new, old_fingerprint="same", new_fingerprint="same")
    assert not planned.conflicts
    assert [(item.target_key.original, item.translation) for item in planned.exact] == [
        ("First", "T1"),
        ("Second", "T2"),
    ]
    result = migrate(TranslationEntryCollection(old), TranslationEntryCollection(new))
    assert result.inherited == 2
    assert [row.translation for row in new] == ["T1", "T2"]


@pytest.mark.parametrize("source_flag,target_flag", [(True, True), (True, False), (False, True)])
def test_migration_never_normalizes_when_either_side_requires_original(source_flag, target_flag, caplog):
    old = [entry("First ", "wrong", flagged=source_flag)]
    new = [entry("First", flagged=target_flag)]
    planned = plan_migration(old, new, old_fingerprint="same", new_fingerprint="same")
    assert not planned.candidates and planned.unmatched == (new[0].identity,)
    assert planned.diagnostics[0].code == "SOURCE_ORIGINAL_MATCH_REQUIRED"
    result = migrate(TranslationEntryCollection(old), TranslationEntryCollection(new))
    assert result.missed == 1 and not new[0].translation
    assert "SOURCE_ORIGINAL_MATCH_REQUIRED" in caplog.text
