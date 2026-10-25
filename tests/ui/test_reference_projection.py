"""Acknowledged external IDs reconcile without accepting unrelated UI edits."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.conftest import make_entry
from transbridge.application.io.identity import EntryRevision, ExternalEntryRef
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import AppContext, CollectionSlot

_APP = QApplication.instance() or QApplication([])


def _snapshot(entries, revision=0, *, project="project", variant="variant"):
    return ProjectionSnapshot(
        f"project:{project}",
        revision,
        revision,
        {
            "project_id": project,
            "variant_id": variant,
            "variant_revision": revision,
            "entries": [
                {
                    "entry_key": entry.identity.to_dict(),
                    "translation": entry.translation,
                    "stage": entry.stage,
                    "external_refs": [ref.to_dict() for ref in entry.external_refs],
                    "revision": entry.revision.value,
                }
                for entry in entries
            ],
        },
    )


def _linked(entry, remote_id, revision):
    return replace(
        entry,
        external_refs=(ExternalEntryRef("paratranz", "project:42", remote_id, (("file_id", 11),)),),
        revision=EntryRevision(revision),
    )


@pytest.fixture
def view(monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    first, other = make_entry("one", translation="译文", stage=3), make_entry("two")
    store = ProjectionStore(_snapshot((first, other)))
    context = AppContext(project_projection=store)
    context.add_slot("one", CollectionSlot("one", TranslationEntryCollection((first,))))
    context.add_slot("duplicate", CollectionSlot("duplicate", TranslationEntryCollection((first,))))
    context.add_slot("other", CollectionSlot("other", TranslationEntryCollection((other,))))
    yield SimpleNamespace(context=context, store=store, first=first, other=other)
    context.close_projection()
    _APP.processEvents()


def test_all_copies_update_before_one_notification_and_unchanged_slot_is_reused(view):
    context = view.context
    context.activate_slot("one")
    untouched = context.slots["other"].collection
    observations = []
    context.collection_changed.connect(lambda _: observations.append(context.authoritative_projection_diverged()))
    updated = _linked(view.first, 101, 1)

    view.store.rebuild(_snapshot((updated, view.other), 1))

    assert observations == [False]
    assert context.slots["other"].collection is untouched
    for key in ("one", "duplicate"):
        collection = context.slots[key].collection
        entry = collection.get(updated.identity)
        assert entry == updated
        assert collection.get_by_external_ref(updated.external_refs[0]) == (entry,)
    view.store.rebuild(_snapshot((updated, view.other), 1))
    assert observations == [False]


def test_receipt_for_inactive_source_does_not_refresh_the_visible_table(view):
    observations = []
    view.context.collection_changed.connect(lambda _: observations.append(True))
    visible = view.context.collection

    view.store.rebuild(_snapshot((_linked(view.first, 101, 1), view.other), 1))

    assert view.context.collection is visible
    assert observations == []
    assert not view.context.authoritative_projection_diverged()


def test_reordered_authority_matches_entries_by_complete_identity(view):
    view.store.rebuild(_snapshot((view.other, _linked(view.first, 101, 1)), 1))

    assert view.context.slots["one"].collection.get(view.first.identity).external_refs[0].opaque_id == 101
    assert not view.context.slots["other"].collection.get(view.other.identity).external_refs
    assert not view.context.authoritative_projection_diverged()


def test_local_reference_edit_is_not_overwritten_or_misreported_saved(view):
    local = _linked(view.first, 999, 1)
    view.context.slots["one"].collection = TranslationEntryCollection((local,))

    view.store.rebuild(_snapshot((_linked(view.first, 101, 1), view.other), 1))

    assert view.context.slots["one"].collection.get(local.identity) is local
    assert view.context.authoritative_projection_diverged()


@pytest.mark.parametrize("ui_revision", [0, 8])
def test_revision_lag_does_not_prevent_sync_and_newer_revision_is_never_rolled_back(view, ui_revision):
    # Inline editing can commit text without advancing its local entry revision.
    authority = replace(view.first, revision=EntryRevision(2))
    view.store.rebuild(_snapshot((authority, view.other), 2))
    visible = replace(view.first, revision=EntryRevision(ui_revision))
    view.context.slots["one"].collection = TranslationEntryCollection((visible,))

    view.store.rebuild(_snapshot((_linked(authority, 101, 3), view.other), 3))

    entry = view.context.slots["one"].collection.get(visible.identity)
    assert entry.external_refs[0].opaque_id == 101
    assert entry.revision.value == max(ui_revision, 3)


@pytest.mark.parametrize("target", [{"project": "another"}, {"variant": "another"}])
def test_other_project_or_variant_publication_does_not_rewrite_installed_entries(view, target):
    original = view.context.slots["one"].collection

    view.store.rebuild(_snapshot((_linked(view.first, 101, 1), view.other), 1, **target))

    assert view.context.slots["one"].collection is original
    assert original.get(view.first.identity).external_refs == ()


def test_multiple_queued_receipts_advance_in_order_and_late_delivery_is_ignored(view):
    first = _snapshot((_linked(view.first, 101, 1), view.other), 1)
    latest = _snapshot((_linked(view.first, 202, 2), view.other), 2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(view.store.rebuild, first).result(timeout=5)
        pool.submit(view.store.rebuild, latest).result(timeout=5)
    assert view.context.slots["one"].collection.get(view.first.identity).external_refs == ()

    _APP.processEvents()
    view.context._project_view._deliver(2, first)

    assert view.context.slots["one"].collection.get(view.first.identity).external_refs[0].opaque_id == 202
    assert not view.context.authoritative_projection_diverged()
