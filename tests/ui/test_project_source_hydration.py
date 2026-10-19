from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui import source_hydration


def _source(location, namespace, originals=("Original",), *, format_id=FormatId.PLUGIN_SSE):
    return SimpleNamespace(
        location=location,
        format_id=format_id,
        source_snapshot=object(),
        entries=tuple(
            TranslationEntry(
                "legacy",
                "same-key",
                original,
                "source translation",
                0,
                "NPC_:FULL",
                string_id=42,
                entry_key=EntryKey(SourceNamespace(namespace), "same-key", original),
                external_refs=(ExternalEntryRef("plugin", namespace, index),),
                metadata=(("category", "NPC_"),),
            ).snapshot()
            for index, original in enumerate(originals)
        ),
    )


def _snapshot(states):
    return ProjectionSnapshot("project:new", 3, 3, {"entries": states})


def test_prepared_slots_preserve_sources_and_exact_identity_without_copying_whole_snapshot(monkeypatch):
    first = _source("first.esp", "source-one", ("A", "B"))
    second = _source("second.xml", "source-two", format_id=FormatId.XML_XT)
    ref = ExternalEntryRef("paratranz", "project-1", 99, (("nested", {"items": [1]}),))
    provenance = Provenance("run", "test", "fixture")
    snapshot = _snapshot([
        {
            "entry_key": first.entries[0].entry_key.to_dict(),
            "translation": "Saved A",
            "stage": 5,
            "revision": 8,
            "external_refs": [ref.to_dict()],
            "provenance": [provenance.to_dict()],
        },
        {
            "entry_key": second.entries[0].entry_key.to_dict(),
            "translation": "Saved second",
            "stage": 1,
            "external_refs": [],
            "inferred_fields": ["external_refs"],
        },
    ])
    monkeypatch.setattr(ProjectionSnapshot, "to_dict", lambda *_: pytest.fail("Whole project copied"))

    slots = source_hydration.prepare_project_slots((first, second), snapshot)

    assert tuple(location for location, _ in slots) == ("first.esp", "second.xml")
    one, two = (slot for _, slot in slots)
    assert one.esp_path == "first.esp" and two.xt_path == "second.xml"
    assert one.source_snapshot is first.source_snapshot
    assert two.source_snapshot is second.source_snapshot
    translated = one.collection.get(first.entries[0].entry_key)
    assert (translated.translation, translated.stage, translated.revision) == ("Saved A", 5, EntryRevision(8))
    assert translated.external_refs == (ref,)
    assert translated.provenance == (provenance,)
    assert translated.string_id == 42 and translated.metadata == first.entries[0].metadata
    assert translated.requires_original_match
    assert one.collection.get(first.entries[1].entry_key).snapshot() == first.entries[1]
    assert two.collection.get(second.entries[0].entry_key).external_refs == second.entries[0].external_refs
    assert first.entries[0].translation == "source translation"


def test_multiple_sources_share_one_state_index_and_construct_each_entry_once(monkeypatch):
    sources = tuple(_source(f"{index}.esp", f"source-{index}") for index in range(3))
    snapshot = _snapshot([
        {"entry_key": source.entries[0].entry_key.to_dict(), "translation": "saved", "stage": 1} for source in sources
    ])
    identity_reads, constructions = [], []
    original_from_dict, original_entry = EntryKey.from_dict, source_hydration.TranslationEntry

    def read_identity(value):
        identity_reads.append(value)
        return original_from_dict(value)

    def create_entry(**values):
        constructions.append(values["entry_key"])
        return original_entry(**values)

    monkeypatch.setattr(EntryKey, "from_dict", read_identity)
    monkeypatch.setattr(source_hydration, "TranslationEntry", create_entry)
    slots = source_hydration.prepare_project_slots(sources, snapshot)

    assert len(identity_reads) == 3
    assert len(constructions) == 3
    assert all(next(iter(slot.collection)).translation == "saved" for _, slot in slots)


def test_duplicate_source_locations_are_rejected_without_mutating_inputs():
    first = _source("same.esp", "first")
    second = _source("same.esp", "second")
    with pytest.raises(ValueError, match="Duplicate project source location"):
        source_hydration.prepare_project_slots((first, second), _snapshot([]))
    assert first.entries[0].translation == second.entries[0].translation == "source translation"


def test_empty_project_has_no_slots():
    assert source_hydration.prepare_project_slots((), _snapshot([])) == ()
