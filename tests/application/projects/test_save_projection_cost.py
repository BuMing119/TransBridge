"""Saving clean content must not rebuild or republish every projected entry."""

from dataclasses import replace
import json

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections.adapters import ProjectProjectionPublisher
from transbridge.application.projections.store import ProjectionStore
from transbridge.application.projects import save_result
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.application.projects.variant_commands import patch_entry_records, patch_entry_states
from transbridge.config.text_encoding import TextEncodings
from transbridge.persistence.v2.variant import (
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantSnapshot,
)


def _saving_facade(*, dirty, legacy=False, rebuild=None):
    project_ref, variant_ref = _refs("save-cost", "main")
    active = _active(project_ref, variant_ref, revision=1, persisted_revision=0 if dirty else 1)
    if not legacy:
        active.project.envelope.data.update(TextEncodings().to_project_data())
    namespace = SourceNamespace("source:save-cost")
    other_namespace = SourceNamespace("source:other")
    active = replace(
        active,
        variant=VariantAggregate(
            VariantSnapshot(
                variant_ref,
                (SourceFingerprint(namespace, "a" * 64), SourceFingerprint(other_namespace, "b" * 64)),
                (
                    VariantEntryState(EntryKey(namespace, "one"), translation="Saved text"),
                    VariantEntryState(EntryKey(other_namespace, "one", "Original")),
                ),
                revision=1,
            )
        ),
    )
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store)
    harness = _harness(active, {}, event=publisher)
    publisher.bind(harness.service)
    facade = GuiProjectCommandFacade(harness.service, projection_rebuild=rebuild)
    return facade, harness, store


@pytest.mark.parametrize("legacy", [False, True])
def test_clean_save_keeps_projection_and_performs_no_entry_serialization(monkeypatch, legacy):
    rebuilds = []
    facade, harness, store = _saving_facade(dirty=False, legacy=legacy, rebuild=lambda: rebuilds.append(True))
    before = store.snapshot()
    notifications = []
    subscription = store.subscribe(notifications.append, replay=False)

    def unexpected_serialization(_entry):
        raise AssertionError("Clean saves must not reconstruct the complete entry projection")

    monkeypatch.setattr(VariantEntryState, "to_dict", unexpected_serialization)
    for _ in range(2):
        saved = facade.save(_context(), include_entry_delta=True)
        assert saved.is_success
        assert saved.value["entry_delta"] == {
            "project_id": "save-cost",
            "variant_id": "main",
            "previous_variant_revision": 1,
            "variant_revision": 1,
            "entry_keys": [],
        }
    assert store.snapshot() is before
    assert notifications == []
    assert rebuilds == []
    assert harness.store.saved_revisions == ([1] if legacy else [])
    subscription.close()


def test_dirty_save_still_publishes_clean_state_and_current_content():
    facade, harness, store = _saving_facade(dirty=True)
    assert store.snapshot().dirty
    notifications = []
    subscription = store.subscribe(notifications.append, replay=False)

    saved = facade.save(_context(), include_entry_delta=True)
    assert saved.is_success
    assert "entry_delta" not in saved.value

    assert not store.snapshot().dirty
    assert len(notifications) == 1
    entries = store.snapshot().to_dict()["values"]["entries"]
    assert any(entry["translation"] == "Saved text" for entry in entries)
    assert harness.store.saved_revisions == [1]
    subscription.close()


def test_save_receipt_spans_committed_edits_and_excludes_later_edits():
    facade, harness, _ = _saving_facade(dirty=False)
    context = _context()
    edited = EntryKey(SourceNamespace("source:other"), "one", "Original")
    later = EntryKey(SourceNamespace("source:save-cost"), "one")
    assert facade.update_entry(edited, context, translation="First", stage=1).is_success
    assert facade.update_entry(edited, context, translation="Second", stage=1).is_success

    harness.store.fail_commit = True
    failed = facade.save(context, include_entry_delta=True)
    assert not failed.is_success and failed.value is None
    assert harness.service.active.persisted_variant_revision == 1
    harness.store.fail_commit = False

    def edit_later():
        assert facade.update_entry(later, context, translation="Later", stage=1).is_success

    harness.store.before_commit = edit_later
    saved = facade.save(context, include_entry_delta=True)

    assert saved.is_success
    receipt = json.loads(json.dumps(saved.value))
    assert receipt["dirty"]
    assert receipt["variant_revision"] == 4
    assert receipt["persisted_variant_revision"] == 3
    assert receipt["entry_delta"] == {
        "project_id": "save-cost",
        "variant_id": "main",
        "previous_variant_revision": 1,
        "variant_revision": 3,
        "entry_keys": [edited.to_dict()],
    }

    harness.store.before_commit = None
    next_save = facade.save(context, include_entry_delta=True)
    assert next_save.is_success
    assert next_save.value["entry_delta"] == {
        "project_id": "save-cost",
        "variant_id": "main",
        "previous_variant_revision": 3,
        "variant_revision": 4,
        "entry_keys": [later.to_dict()],
    }


def test_save_receipt_omits_delta_when_the_project_revision_changes():
    facade, harness, _ = _saving_facade(dirty=False)
    active = harness.service.active
    context = _context()
    changed_project = replace(active.project, envelope=replace(active.project.envelope, revision=1))
    assert harness.service.commit_active_content(
        changed_project,
        active.variant.snapshot(),
        context,
        expected_project_revision=0,
        expected_variant_revision=1,
    ).is_success

    saved = facade.save(context, include_entry_delta=True)

    assert saved.is_success
    assert saved.value["persisted_project_revision"] == 1
    assert "entry_delta" not in saved.value


@pytest.mark.parametrize("use_facade", [False, True])
def test_save_keeps_the_original_summary_by_default(use_facade):
    facade, harness, _ = _saving_facade(dirty=False)
    context = _context()
    key = EntryKey(SourceNamespace("source:save-cost"), "one")
    assert facade.update_entry(key, context, translation="Edited", stage=1).is_success
    save = facade.save if use_facade else harness.service.save_active

    for _ in range(2):
        result = save(context)
        assert result.is_success
        assert result.value == harness.service.active.summary()


def test_oversized_save_uses_full_dictionary_fallback(monkeypatch):
    monkeypatch.setattr(save_result, "MAX_SAVE_ENTRY_DELTA_KEYS", 1)
    facade, harness, _ = _saving_facade(dirty=False)
    context = _context()
    source_key = EntryKey(SourceNamespace("source:save-cost"), "one")
    other_key = EntryKey(SourceNamespace("source:other"), "one", "Original")
    assert facade.replace_entry_states({source_key: ("Edited", 1), other_key: ("Also edited", 1)}, context).is_success

    saved = facade.save(context, include_entry_delta=True)

    assert saved.is_success
    assert saved.value == harness.service.active.summary()
    assert harness.store.saved_revisions == [2]


@pytest.mark.parametrize("patch", [patch_entry_states, patch_entry_records])
def test_empty_patch_preserves_entries_without_reading_the_full_variant(patch):
    class UnreadEntries(tuple):
        def __iter__(self):
            raise AssertionError("An empty result patch must not traverse the full Variant")

    namespace = SourceNamespace("source:save-cost")
    entries = UnreadEntries((VariantEntryState(EntryKey(namespace, "one")),))
    assert patch(entries, {}) is entries
