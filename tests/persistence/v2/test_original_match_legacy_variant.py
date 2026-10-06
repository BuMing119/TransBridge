"""Legacy variant caches must preserve original-disambiguated identities."""

from dataclasses import replace

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.variant_store import VariantStore


def entries():
    return [
        TranslationEntry("same", "same", original, translation, stage, None, requires_original_match=True)
        for original, translation, stage in [("A", "First", 1), ("B", "Second", 3)]
    ]


def test_legacy_variant_round_trip_keeps_independent_translations_stages_and_labels(tmp_path):
    source = entries()
    store = VariantStore(tmp_path / "current.json")
    keys = [entry.identity.serialize() for entry in source]
    labels = {keys[0]: {"first"}, keys[1]: {"second"}}
    store.collect_from(source, labels, {})
    store.save()
    loaded = VariantStore.load(store._path)
    assert loaded.labels == labels
    assert loaded.translations == dict(zip(keys, ["First", "Second"], strict=True))
    runtime = [replace(entry, translation="", stage=0) for entry in source]
    with pytest.warns(DeprecationWarning):
        assert loaded.apply_to(runtime) == 2
    assert [(entry.translation, entry.stage) for entry in runtime] == [("First", 1), ("Second", 3)]


def test_plain_legacy_id_never_overwrites_ambiguous_entries(tmp_path):
    store = VariantStore(tmp_path / "current.json")
    store.translations = {"same": "Unproved"}
    store.entry_states = {"same": {"stage": 5}}
    runtime = entries()
    with pytest.warns(DeprecationWarning, match="lossy compatibility path"):
        assert store.apply_to(runtime) == 0
    assert [entry.translation for entry in runtime] == ["First", "Second"]


def test_missing_flagged_cache_uses_exact_baseline(tmp_path):
    runtime = entries()
    baseline = [replace(entry, translation="", stage=0) for entry in runtime]
    store = VariantStore(tmp_path / "current.json")
    key = runtime[1].identity.serialize()
    store.translations = {key: "Restored"}
    store.entry_states = {key: {"stage": 5}}
    with pytest.warns(DeprecationWarning):
        assert store.apply_to(runtime, source_baseline=baseline) == 2
    assert [(entry.translation, entry.stage) for entry in runtime] == [("", 0), ("Restored", 5)]
