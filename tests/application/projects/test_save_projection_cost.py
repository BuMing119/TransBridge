"""Saving clean content must not rebuild or republish every projected entry."""

from dataclasses import replace

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections.adapters import ProjectProjectionPublisher
from transbridge.application.projections.store import ProjectionStore
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.application.projects.variant_commands import patch_entry_records, patch_entry_states
from transbridge.persistence.v2.variant import (
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantSnapshot,
)


def _saving_facade(*, dirty):
    project_ref, variant_ref = _refs("save-cost", "main")
    active = _active(project_ref, variant_ref, revision=1, persisted_revision=0 if dirty else 1)
    namespace = SourceNamespace("source:save-cost")
    active = replace(
        active,
        variant=VariantAggregate(
            VariantSnapshot(
                variant_ref,
                (SourceFingerprint(namespace, "a" * 64),),
                (VariantEntryState(EntryKey(namespace, "one"), translation="Saved text"),),
                revision=1,
            )
        ),
    )
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store)
    harness = _harness(active, {}, event=publisher)
    publisher.bind(harness.service)
    facade = GuiProjectCommandFacade(harness.service, publisher.rebuild)
    return facade, harness, store


def test_clean_save_keeps_projection_and_performs_no_entry_serialization(monkeypatch):
    facade, harness, store = _saving_facade(dirty=False)
    before = store.snapshot()
    notifications = []
    subscription = store.subscribe(notifications.append, replay=False)

    def unexpected_serialization(_entry):
        raise AssertionError("Clean saves must not reconstruct the complete entry projection")

    monkeypatch.setattr(VariantEntryState, "to_dict", unexpected_serialization)
    assert facade.save(_context()).is_success
    assert facade.save(_context()).is_success
    assert store.snapshot() is before
    assert notifications == []
    assert harness.store.saved_revisions == []
    subscription.close()


def test_dirty_save_still_publishes_clean_state_and_current_content():
    facade, harness, store = _saving_facade(dirty=True)
    assert store.snapshot().dirty
    notifications = []
    subscription = store.subscribe(notifications.append, replay=False)

    assert facade.save(_context()).is_success

    assert not store.snapshot().dirty
    assert len(notifications) == 1
    assert store.snapshot().to_dict()["values"]["entries"][0]["translation"] == "Saved text"
    assert harness.store.saved_revisions == [1]
    subscription.close()


@pytest.mark.parametrize("patch", [patch_entry_states, patch_entry_records])
def test_empty_patch_preserves_entries_without_reading_the_full_variant(patch):
    class UnreadEntries(tuple):
        def __iter__(self):
            raise AssertionError("An empty result patch must not traverse the full Variant")

    namespace = SourceNamespace("source:save-cost")
    entries = UnreadEntries((VariantEntryState(EntryKey(namespace, "one")),))
    assert patch(entries, {}) is entries
