"""Unchanged immutable rows survive projection updates without being serialized again."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections.adapters import ProjectProjectionPublisher
from transbridge.application.projections.models import (
    ProjectionSnapshot,
    _FrozenArray,
    _FrozenMapping,
    freeze_projection_value,
)
from transbridge.application.projections.project_entries import ProjectEntryProjection
from transbridge.application.projections.store import ProjectionStore
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.persistence.v2.variant import (
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantSnapshot,
)


def _snapshot(size=20):
    project, variant = _refs("entry-reuse", "main")
    namespace = SourceNamespace("source:entry-reuse")
    entries = tuple(
        VariantEntryState(EntryKey(namespace, f"{index:04}"), translation="original") for index in range(size)
    )
    snapshot = VariantSnapshot(variant, (SourceFingerprint(namespace, "a" * 64),), entries)
    active = replace(_active(project, variant), variant=VariantAggregate(snapshot))
    return snapshot, active


def _count_serializations(monkeypatch):
    calls = []
    serialize = VariantEntryState.to_dict

    def counted(entry):
        calls.append(entry.entry_key)
        return serialize(entry)

    monkeypatch.setattr(VariantEntryState, "to_dict", counted)
    return calls


def test_single_entry_edit_reuses_other_rows_and_still_notifies_every_listener(monkeypatch):
    snapshot, active = _snapshot(1000)
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store)
    harness = _harness(active, {}, event=publisher)
    publisher.bind(harness.service)
    facade = GuiProjectCommandFacade(harness.service, publisher.rebuild)
    before = store.snapshot()
    old_rows = tuple(before.values["entries"])
    calls = _count_serializations(monkeypatch)
    notifications = []
    subscription = store.subscribe(notifications.append, replay=False)

    result = facade.update_entry(snapshot.entries[0].entry_key, _context(), translation="one edit")

    assert result.is_success
    assert calls == [snapshot.entries[0].entry_key]
    after = store.snapshot()
    new_rows = tuple(after.values["entries"])
    assert new_rows[0] is not old_rows[0]
    assert all(new is old for new, old in zip(new_rows[1:], old_rows[1:], strict=True))
    assert after.revision == before.revision + 1 and after.dirty
    assert len(notifications) == 1 and notifications[0] is after
    assert before.to_dict()["values"]["entries"][0]["translation"] == "original"
    assert after.to_dict()["values"]["entries"][0]["translation"] == "one edit"
    subscription.close()


def test_bulk_edit_serializes_changed_rows_and_keeps_unchanged_rows(monkeypatch):
    snapshot, _ = _snapshot()
    projector = ProjectEntryProjection()
    before = projector.rows(snapshot)
    calls = _count_serializations(monkeypatch)
    changed = tuple(
        replace(entry, translation="bulk edit") if index < 12 else entry for index, entry in enumerate(snapshot.entries)
    )
    after = projector.rows(replace(snapshot, entries=changed, revision=1))

    assert calls == [entry.entry_key for entry in changed[:12]]
    assert all(new is old for new, old in zip(after[12:], before[12:], strict=True))


@pytest.mark.parametrize("target", ["other-project", "other-variant", "closed"])
def test_variant_project_switch_and_close_do_not_reuse_another_target_cache(monkeypatch, target):
    snapshot, _ = _snapshot()
    projector = ProjectEntryProjection()
    original_rows = projector.rows(snapshot)
    calls = _count_serializations(monkeypatch)
    if target == "closed":
        assert projector.rows(None) == ()
    else:
        _, ref = _refs("other" if target == "other-project" else "entry-reuse", "other")
        switched = replace(snapshot, ref=ref)
        switched_rows = projector.rows(switched)
        assert all(new is not old for new, old in zip(switched_rows, original_rows, strict=True))
        assert len(calls) == len(snapshot.entries)
        calls.clear()

    returned = projector.rows(snapshot)
    assert len(calls) == len(snapshot.entries)
    assert all(new is not old for new, old in zip(returned, original_rows, strict=True))


def test_deleted_entry_is_evicted_and_restore_publishes_new_content_at_lower_revision(monkeypatch):
    snapshot, active = _snapshot(3)
    lifecycle = SimpleNamespace(active=active)
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store, lifecycle)
    publisher.rebuild()
    before = store.snapshot()
    retained = snapshot.entries[1:]
    removed = replace(snapshot, entries=tuple(reversed(retained)), revision=2)
    lifecycle.active = replace(active, variant=VariantAggregate(removed))
    calls = _count_serializations(monkeypatch)
    publisher.rebuild()
    assert calls == []
    assert [row["entry_key"]["local_key"] for row in store.snapshot().to_dict()["values"]["entries"]] == [
        "0001",
        "0002",
    ]

    lifecycle.active = active
    publisher.rebuild()
    assert calls == [snapshot.entries[0].entry_key]
    assert store.snapshot().revision == before.revision
    assert store.snapshot().to_dict() == before.to_dict()


def test_same_snapshot_save_reuses_rows_but_updates_persisted_metadata(monkeypatch):
    snapshot, active = _snapshot()
    active = replace(active, variant=VariantAggregate(replace(snapshot, revision=1)), persisted_variant_revision=0)
    lifecycle = SimpleNamespace(active=active)
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store, lifecycle)
    publisher.rebuild()
    assert store.snapshot().dirty
    calls = _count_serializations(monkeypatch)
    previous = tuple(store.snapshot().values["entries"])
    lifecycle.active = replace(active, persisted_variant_revision=1)
    publisher.rebuild()
    assert calls == []
    assert not store.snapshot().dirty
    assert all(a is b for a, b in zip(previous, store.snapshot().values["entries"], strict=True))


def test_reusing_frozen_values_cannot_expose_mutable_containers_or_change_history():
    mutable = {"children": [{"translation": "original"}]}
    frozen = freeze_projection_value(mutable)
    first = ProjectionSnapshot("history", 0, 0, {"entry": frozen})
    second = ProjectionSnapshot("history", 1, 0, first.values)
    mutable["children"][0]["translation"] = "external mutation"
    copied = second.to_dict()
    copied["values"]["entry"]["children"][0]["translation"] = "copy mutation"
    assert second.values is first.values
    assert first.to_dict()["values"]["entry"]["children"][0]["translation"] == "original"
    assert second.to_dict()["values"]["entry"]["children"][0]["translation"] == "original"


def test_frozen_container_constructors_also_freeze_external_nested_values():
    mutable = {"translation": ["original"]}
    external_array = _FrozenArray((mutable,))
    external_mapping = _FrozenMapping((("entry", mutable),))
    snapshot = ProjectionSnapshot("constructed", 0, 0, {"rows": external_array, "metadata": external_mapping})
    mutable["translation"].append("later edit")
    assert snapshot.to_dict()["values"] == {
        "rows": [{"translation": ["original"]}],
        "metadata": {"entry": {"translation": ["original"]}},
    }


def test_mark_persisted_reuses_frozen_values_and_notifies_without_thawing_content(monkeypatch):
    previous = ProjectionSnapshot("saved", 1, 0, {"entries": [{"translation": "text"}]})
    store = ProjectionStore(previous)
    notifications = []
    subscription = store.subscribe(notifications.append, replay=False)

    def unexpected_copy(_snapshot):
        raise AssertionError("Persistence metadata acknowledgement must not copy all entry values")

    monkeypatch.setattr(ProjectionSnapshot, "to_dict", unexpected_copy)
    result = store.mark_persisted(1)

    assert result.applied and not result.snapshot.dirty
    assert result.snapshot.values is previous.values
    assert previous.dirty
    assert notifications == [result.snapshot]
    subscription.close()
