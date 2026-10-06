from dataclasses import replace

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def _entry(original: str, **kwargs) -> TranslationEntry:
    return TranslationEntry("legacy-id", "shared-key", original, "", 0, "QUST:CNAM", **kwargs)


def test_ordinary_identity_retains_existing_wire_format() -> None:
    entry = _entry("Hello")

    assert not entry.requires_original_match
    assert entry.identity.serialize() == '["legacy:v1","shared-key"]'
    assert entry.identity.to_dict() == {"namespace": "legacy:v1", "local_key": "shared-key"}
    assert EntryKey.deserialize(entry.identity.serialize()) == entry.identity
    assert TranslationEntry.from_dict(entry.to_dict()) == entry
    legacy = entry.to_dict()
    legacy.pop("requires_original_match")
    assert not TranslationEntry.from_dict(legacy).requires_original_match


@pytest.mark.parametrize("original", ["", "first", "中文\n第二行", 'quotes " and \\'])
def test_original_discriminator_round_trips_without_changing_key_or_id(original: str) -> None:
    entry = _entry(original, requires_original_match=True)

    assert (entry.id, entry.key) == ("legacy-id", "shared-key")
    assert entry.identity.original == original
    assert EntryKey.deserialize(entry.identity.serialize()) == entry.identity
    assert EntryKey.from_dict(entry.identity.to_dict()) == entry.identity
    assert TranslationEntry.from_dict(entry.to_dict()) == entry
    assert entry.snapshot().requires_original_match
    assert entry.snapshot().entry_key == entry.identity


def test_duplicate_legacy_keys_with_different_originals_remain_separate() -> None:
    first = _entry("first", requires_original_match=True)
    second = _entry("second", requires_original_match=True)
    collection = TranslationEntryCollection((first, second))

    assert len(collection) == 2
    assert collection.get(first.identity) is first
    assert collection.get(second.identity) is second
    assert collection.get_by_id("legacy-id") is None
    with pytest.warns(RuntimeWarning, match="ambiguous"):
        assert collection.get("shared-key") is None


def test_identity_order_handles_ordinary_empty_and_nonempty_originals() -> None:
    namespace = SourceNamespace.legacy()
    ordinary = EntryKey(namespace, "key")
    empty = EntryKey(namespace, "key", "")
    text = EntryKey(namespace, "key", "text")

    assert len({ordinary, empty, text}) == 3
    assert sorted((text, empty, ordinary)) == [ordinary, empty, text]


def test_reconstruction_infers_flag_from_complete_identity() -> None:
    identity = EntryKey(SourceNamespace("source:test"), "shared-key", "Hello")
    entry = _entry("Hello", entry_key=identity)
    payload = entry.to_dict()
    payload.pop("requires_original_match")

    assert entry.requires_original_match
    assert TranslationEntry.from_dict(payload).requires_original_match
    assert replace(entry.snapshot(), requires_original_match=False).requires_original_match
    assert replace(entry, translation="译文").identity == identity


def test_original_identity_cannot_silently_drift() -> None:
    entry = _entry("Hello", requires_original_match=True)

    with pytest.raises(AttributeError, match="cannot be changed"):
        entry.original = "other"
    with pytest.raises(AttributeError, match="identity state"):
        entry.requires_original_match = False
    with pytest.raises(ValueError, match="identity original"):
        replace(entry, original="other")
    with pytest.raises(ValueError, match="identity original"):
        replace(entry.snapshot(), original="other")


@pytest.mark.parametrize("value", ["false", 1, None])
def test_original_match_flag_requires_boolean(value) -> None:
    with pytest.raises(TypeError, match="boolean"):
        _entry("Hello", requires_original_match=value)


def test_snapshot_cannot_enable_original_matching_without_discriminator() -> None:
    with pytest.raises(ValueError, match="identity original"):
        replace(_entry("Hello").snapshot(), requires_original_match=True)


def test_original_match_comparison_is_exact() -> None:
    first = _entry("Hello", requires_original_match=True)
    second = _entry("Hello ", requires_original_match=True)
    assert first.identity != second.identity


def test_incremental_add_and_json_reload_preserve_qualified_legacy_siblings(tmp_path):
    from dataclasses import replace

    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    first = TranslationEntry("same", "same", "First", "T1", 1, "", requires_original_match=True)
    second = TranslationEntry("same", "same", "Second", "T2", 1, "", requires_original_match=True)
    collection = TranslationEntryCollection()
    collection.add(first)
    collection.add(second)
    collection.add(replace(first, translation="T1 updated"))
    assert len(collection) == 2
    assert collection.get(second.identity).translation == "T2"
    path = tmp_path / "entries.json"
    collection.to_json_file(path)
    restored = TranslationEntryCollection.from_json_file(path)
    assert [(item.identity, item.translation) for item in restored] == [
        (first.identity, "T1 updated"),
        (second.identity, "T2"),
    ]


def test_ordinary_legacy_add_does_not_rebind_to_a_qualified_identity():
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    qualified = TranslationEntry("same", "same", "First", "T1", 1, "", requires_original_match=True)
    ordinary = TranslationEntry("same", "same", "Other", "T2", 1, "")
    collection = TranslationEntryCollection([qualified])
    collection.add(ordinary)
    assert collection.get(qualified.identity).translation == "T1"
    assert collection.get(ordinary.identity).translation == "T2"
