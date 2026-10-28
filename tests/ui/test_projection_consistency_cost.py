"""Saving reuses delivered evidence without concealing compatibility edits."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import replace
import gc
from threading import Event, Thread
from types import SimpleNamespace
import weakref

import pytest

from tests.conftest import make_entry
from tests.ui.test_content_projection_merge import _APP, _snapshot, view_factory as view_factory  # noqa: F401
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.converter.collection_changes import JOURNAL_LIMIT
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import projection_consistency
from transbridge.ui.context import CollectionSlot
from transbridge.ui.main_window import MainWindow


def _count_content_reads(monkeypatch):
    reads = []
    real = projection_consistency._entry_state

    def state(entry):
        reads.append(entry.identity)
        return real(entry)

    monkeypatch.setattr(projection_consistency, "_entry_state", state)
    return reads


def test_two_clean_saves_reuse_delivery_without_visiting_any_of_2000_rows(view_factory, monkeypatch):  # noqa: F811
    entries = tuple(make_entry(str(index)) for index in range(2000))
    view = view_factory(entries)
    collection = TranslationEntryCollection(entries)
    view.context._slots = {"all": CollectionSlot("all", collection)}
    view.store.rebuild(_snapshot(entries, revision=1))
    assert not view.context.dirty
    reads = _count_content_reads(monkeypatch)
    real_iter = TranslationEntryCollection.__iter__
    visits = []

    def tracked_iter(self):
        for entry in real_iter(self):
            visits.append(entry.identity)
            yield entry

    monkeypatch.setattr(TranslationEntryCollection, "__iter__", tracked_iter)
    saved = []
    host = SimpleNamespace(
        _ctx=view.context,
        _runtime_context=SimpleNamespace(run_id="save"),
        _project_commands=SimpleNamespace(save=lambda context: saved.append(context) or "saved"),
    )
    assert MainWindow._save_current_project(host) == "saved"
    assert MainWindow._save_current_project(host) == "saved"
    assert len(saved) == 2
    assert visits == reads == []

    with pytest.warns(DeprecationWarning):
        entries[911].translation = "unsubmitted"
    rejected = MainWindow._save_current_project(host)
    assert not rejected.is_success
    assert rejected.diagnostics[0].code == "PROJECTION_AUTHORITY_DIVERGED"
    assert reads == [entries[911].identity]
    assert visits == []
    assert len(saved) == 2


def test_one_mutable_entry_invalidates_every_owner_and_reversion_becomes_clean(view_factory, monkeypatch):  # noqa: F811
    view = view_factory()
    entry = view.entries[0]
    assert not view.context.authoritative_projection_diverged()
    reads = _count_content_reads(monkeypatch)
    with pytest.warns(DeprecationWarning):
        entry.stage = 9
    assert view.context.authoritative_projection_diverged()
    assert reads == [entry.identity, entry.identity]
    reads.clear()
    assert view.context.authoritative_projection_diverged()
    assert reads == []
    with pytest.warns(DeprecationWarning):
        entry.stage = 3
    assert not view.context.authoritative_projection_diverged()
    assert reads == [entry.identity, entry.identity]


def test_same_collection_public_add_remove_and_replace_are_not_hidden(view_factory):  # noqa: F811
    view = view_factory()
    collection = view.context.slots["1"].collection
    entry = view.entries[1]
    assert not view.context.authoritative_projection_diverged()
    collection.remove(entry.identity)
    assert view.context.authoritative_projection_diverged()
    collection.add(entry)
    assert not view.context.authoritative_projection_diverged()
    extra = make_entry("extra")
    collection.add(extra)
    assert view.context.authoritative_projection_diverged()
    collection.remove(extra.identity)
    assert not view.context.authoritative_projection_diverged()
    with pytest.warns(DeprecationWarning):
        collection.add(replace(entry, translation="draft"))
    assert view.context.authoritative_projection_diverged()


def test_slot_removal_replacement_and_readdition_invalidate_membership(view_factory):  # noqa: F811
    view = view_factory()
    assert not view.context.authoritative_projection_diverged()
    slot = view.context.slots.pop("1")
    assert view.context.authoritative_projection_diverged()
    view.context.slots["renamed"] = slot
    assert not view.context.authoritative_projection_diverged()
    slot.collection = TranslationEntryCollection((replace(view.entries[1], translation="draft"),))
    assert view.context.authoritative_projection_diverged()


def test_nested_reference_metadata_mutation_remains_detectable_without_a_setter(view_factory):  # noqa: F811
    ref = ExternalEntryRef("paratranz", "project:1", 1, (("nested", {"values": [1]}),))
    entry = replace(make_entry("one"), external_refs=(ref,))
    view = view_factory((entry, make_entry("other")))
    assert not view.context.authoritative_projection_diverged()
    dict(ref.metadata)["nested"]["values"].append(2)
    assert view.context.authoritative_projection_diverged()
    dict(ref.metadata)["nested"]["values"].pop()
    assert not view.context.authoritative_projection_diverged()


def test_lagging_reader_scans_once_after_bounded_journal_overflow(view_factory, monkeypatch):  # noqa: F811
    view = view_factory()
    entry = view.entries[1]
    collection = view.context.slots["1"].collection
    assert not view.context.authoritative_projection_diverged()
    with pytest.warns(DeprecationWarning):
        for index in range(JOURNAL_LIMIT + 5):
            entry.translation = str(index)
        entry.translation = ""
    assert len(collection.change_journal._events) == JOURNAL_LIMIT
    reads = _count_content_reads(monkeypatch)
    assert not view.context.authoritative_projection_diverged()
    assert reads == [entry.identity]
    reads.clear()
    assert not view.context.authoritative_projection_diverged()
    assert reads == []


@pytest.mark.parametrize("clone", [copy, deepcopy])
def test_entry_copies_do_not_inherit_collection_subscriptions(clone):
    entry = make_entry("one")
    collection = TranslationEntryCollection((entry,))
    version = collection.change_journal.version
    detached = clone(entry)
    with pytest.warns(DeprecationWarning):
        detached.translation = "detached"
    assert collection.change_journal.version == version
    copied_collection = TranslationEntryCollection((detached,))
    with pytest.warns(DeprecationWarning):
        detached.stage = 3
    assert collection.change_journal.version == version
    assert copied_collection.change_journal.version > 1
    assert entry.snapshot().translation == ""


def test_weak_entry_subscriptions_do_not_keep_a_collection_or_journal_alive():
    entry = make_entry("one")
    collection = TranslationEntryCollection((entry,))
    owner, journal = weakref.ref(collection), weakref.ref(collection.change_journal)
    del collection
    gc.collect()
    assert owner() is journal() is None
    with pytest.warns(DeprecationWarning):
        entry.translation = "still usable"


def test_new_collection_subscribing_while_a_setter_waits_cannot_cache_a_stale_clean_state():
    entry = make_entry("one")
    first = TranslationEntryCollection((entry,))
    entered, release = Event(), Event()
    original_lock = first.change_journal.lock
    errors = []

    class PausedLock:
        def __enter__(self):
            entered.set()
            assert release.wait(3)
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    def mutate():
        try:
            entry.translation = "draft"
        except Exception as exc:
            errors.append(exc)

    first.change_journal.lock = PausedLock()
    worker = Thread(target=mutate)
    snapshot = _snapshot((entry,))
    consistency = projection_consistency.ProjectionConsistency()
    with pytest.warns(DeprecationWarning):
        worker.start()
        try:
            assert entered.wait(1)
            second = TranslationEntryCollection((entry,))
            slots = (CollectionSlot("new owner", second),)
            assert not consistency.diverged(snapshot, slots)
        finally:
            release.set()
            worker.join(3)
    assert not worker.is_alive()
    assert errors == []
    assert entry.translation == "draft"
    assert consistency.diverged(snapshot, slots)


def test_an_edit_during_a_background_check_is_observed_before_it_returns(view_factory, monkeypatch):  # noqa: F811
    view = view_factory()
    assert not view.context.authoritative_projection_diverged()
    binding = view.context._project_view
    compared, release = Event(), Event()
    real = binding._diverged_snapshot
    reads = []

    def compare(snapshot):
        if not reads:
            compared.set()
            assert release.wait(3)
        reads.append(snapshot)
        return real(snapshot)

    monkeypatch.setattr(binding, "_diverged_snapshot", compare)
    with ThreadPoolExecutor(max_workers=1) as pool:
        check = pool.submit(view.context.authoritative_projection_diverged)
        assert compared.wait(1)
        with pytest.warns(DeprecationWarning):
            view.entries[0].translation = "draft while checking"
        release.set()
        assert check.result(timeout=1)


def test_background_wait_reuses_the_newly_delivered_evidence(view_factory, monkeypatch):  # noqa: F811
    view = view_factory()
    assert not view.context.authoritative_projection_diverged()
    updated = replace(view.entries[0], translation="new authority", stage=5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(view.store.rebuild, _snapshot((updated, view.entries[1]), 1)).result(timeout=1)
        result = pool.submit(view.context.authoritative_projection_diverged)
        _APP.processEvents()
        assert result.result(timeout=1) is False
    reads = _count_content_reads(monkeypatch)
    assert not view.context.authoritative_projection_diverged()
    assert reads == []
