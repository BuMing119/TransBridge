from __future__ import annotations

from dataclasses import fields

import pytest

from transbridge.application.io.identity import ExternalEntryRef, Provenance
from transbridge.application.io.legacy_migration import prepare_legacy_migration
from transbridge.application.io.migration_snapshot import detached_entries
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.coordinators.migration_coordinator import _Target
from transbridge.ui.projection_types import CollectionSlot


def _entry():
    return TranslationEntry(
        "id",
        "key",
        "Original",
        "",
        0,
        "NPC_:FULL",
        string_id=7,
        form_id_with_plugin="00000800|plugin.esp",
        dsd_type="NPC_ FULL",
        dsd_index=2,
        editor_id="Npc",
        external_refs=(ExternalEntryRef("test", "scope", 1, (("hint", "value"),)),),
        provenance=(Provenance("run", "actor", "test", metadata=(("hint", "value"),)),),
        metadata=(("simple", ("immutable", 7)),),
    )


def test_detached_entry_preserves_all_fields_and_shares_only_immutable_payloads():
    entry = _entry()
    snapshot = detached_entries((entry,))[0]
    assert snapshot is not entry
    for field in fields(entry):
        assert getattr(snapshot, field.name) == getattr(entry, field.name)
    assert snapshot.identity is entry.identity
    assert snapshot.revision is entry.revision
    assert snapshot.metadata is entry.metadata
    assert snapshot.external_refs is entry.external_refs
    assert snapshot.provenance is entry.provenance
    with pytest.warns(DeprecationWarning):
        entry.original = "Edited"
    assert snapshot.original == "Original"
    with pytest.warns(DeprecationWarning):
        snapshot.translation = "Result"
    assert entry.translation == ""


@pytest.mark.parametrize("where", ["metadata", "external_refs", "provenance"])
def test_nested_mutable_metadata_is_detached_and_detected_as_stale(where):
    from types import SimpleNamespace

    entry = _entry()
    nested = {"items": ["before"]}
    metadata = (("nested", nested),)
    if where == "metadata":
        value = metadata
    elif where == "external_refs":
        value = (ExternalEntryRef("test", "scope", 1, metadata),)
    else:
        value = (Provenance("run", "actor", "test", metadata=metadata),)
    object.__setattr__(entry, where, value)
    slot = CollectionSlot("test", TranslationEntryCollection([entry]))
    target = _Target.capture(slot)
    context = SimpleNamespace(slots={"test": slot})
    assert target.is_current(context)
    nested["items"].append("after")
    assert not target.is_current(context)
    saved = getattr(target.entries[0], where)
    saved_metadata = saved if where == "metadata" else saved[0].metadata
    assert dict(saved_metadata)["nested"] == {"items": ["before"]}


def test_no_source_legacy_draft_still_owns_mutable_entries():
    entry = _entry()
    target = TranslationEntryCollection([entry])
    draft = prepare_legacy_migration(target)
    copied = next(iter(draft.collection))
    assert copied == entry and copied is not entry
    with pytest.warns(DeprecationWarning):
        copied.translation = "Draft edit"
    assert entry.translation == ""
