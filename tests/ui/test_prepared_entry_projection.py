"""Worker-prepared authority updates must rebase edits before Qt publication."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import get_ident

import pytest

from tests.ui.test_content_projection_merge import _APP, _snapshot, _visible, view_factory as content_view_factory
from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryRevision, Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import entry_projection, project_view_binding
from transbridge.ui.context import CollectionSlot

view_factory = content_view_factory


def _queue(view, snapshot):
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(view.store.rebuild, snapshot).result(timeout=5)


def _updated(view, *, translation="已持久化译文", revision=1):
    original, other = view.entries
    current = replace(original, translation=translation, stage=5, revision=EntryRevision(revision))
    return current, _snapshot((current, other), revision)


def test_expensive_preparation_runs_in_worker_and_actual_publication_runs_on_qt_thread(view_factory, monkeypatch):
    view = view_factory()
    original = view.entries[0]
    current, snapshot = _updated(view)
    gui_thread = get_ident()
    prepare_threads, collection_threads, apply_threads, notification_threads = [], [], [], []
    real_prepare = project_view_binding.prepare_projected_entries
    real_collection = TranslationEntryCollection.__init__
    real_apply = entry_projection.PreparedEntryProjection.apply

    def prepare(*args, **kwargs):
        prepare_threads.append(get_ident())
        return real_prepare(*args, **kwargs)

    def collection(self, *args, **kwargs):
        collection_threads.append(get_ident())
        return real_collection(self, *args, **kwargs)

    def apply(self):
        apply_threads.append(get_ident())
        return real_apply(self)

    monkeypatch.setattr(project_view_binding, "prepare_projected_entries", prepare)
    monkeypatch.setattr(TranslationEntryCollection, "__init__", collection)
    monkeypatch.setattr(entry_projection.PreparedEntryProjection, "apply", apply)
    view.context.collection_changed.connect(lambda _: notification_threads.append(get_ident()))

    _queue(view, snapshot)
    assert _visible(view) is original
    assert prepare_threads and all(thread != gui_thread for thread in prepare_threads)
    assert collection_threads and all(thread != gui_thread for thread in collection_threads)
    assert apply_threads == notification_threads == []

    _APP.processEvents()

    assert _visible(view) == _visible(view, "duplicate") == current
    assert apply_threads == notification_threads == [gui_thread]
    assert not view.context.authoritative_projection_diverged()


def test_collection_replacement_after_prepare_preserves_new_uncommitted_text(view_factory):
    view = view_factory()
    original = view.entries[0]
    current, snapshot = _updated(view)
    _queue(view, snapshot)
    local = replace(original, translation="准备后新编辑", stage=9)
    view.context.slots["0"].collection = TranslationEntryCollection((local,))

    _APP.processEvents()

    assert (_visible(view).translation, _visible(view).stage) == (local.translation, local.stage)
    assert _visible(view, "duplicate") == current
    assert view.context.authoritative_projection_diverged()


def test_slot_replacement_after_prepare_never_publishes_into_the_detached_slot(view_factory):
    view = view_factory()
    original = view.entries[0]
    detached = view.context.slots["0"]
    current, snapshot = _updated(view)
    _queue(view, snapshot)
    local = replace(original, translation="新slot的本地工作", stage=9)
    replacement = CollectionSlot("replacement", TranslationEntryCollection((local,)))
    view.context.slots["0"] = replacement

    _APP.processEvents()

    assert view.context.slots["0"] is replacement
    assert (_visible(view).translation, _visible(view).stage) == (local.translation, local.stage)
    assert detached.collection.get(original.identity) is original
    assert _visible(view, "duplicate") == current


@pytest.mark.parametrize("field", ["translation", "context", "original"])
def test_in_place_legacy_edit_after_prepare_is_preserved_before_publication(view_factory, field):
    view = view_factory()
    original = view.entries[0]
    current, snapshot = _updated(view)
    _queue(view, snapshot)
    with pytest.warns(DeprecationWarning, match="Direct TranslationEntry"):
        setattr(original, field, "准备后原地新编辑")

    _APP.processEvents()

    assert getattr(_visible(view), field) == "准备后原地新编辑"
    if field == "translation":
        assert _visible(view).stage == original.stage
        assert view.context.authoritative_projection_diverged()
    else:
        assert (_visible(view).translation, _visible(view).stage) == (current.translation, current.stage)


def test_collection_apply_after_prepare_is_not_overwritten_by_a_prepared_entry_clone(view_factory):
    view = view_factory()
    original = view.entries[0]
    current, snapshot = _updated(view)
    _queue(view, snapshot)
    change = ChangeSet(
        "local-run",
        (EntryPatch.create(original.identity, translation="Collection.apply提交的本地新译文", stage=9),),
        ((original.identity, original.revision),),
        Provenance("local-run", "user", "inline"),
    )
    context = RequestContext(
        "user", run_id="local-run", permissions=frozenset({"entry.translation.write", "entry.stage.write"})
    )
    result = view.context.slots["0"].collection.apply(change, context)
    assert result.status is MutationStatus.APPLIED

    _APP.processEvents()

    assert (_visible(view).translation, _visible(view).stage) == ("Collection.apply提交的本地新译文", 9)
    assert _visible(view, "duplicate") == current
    assert view.context.authoritative_projection_diverged()


def test_membership_change_after_prepare_does_not_restore_a_removed_entry(view_factory):
    view = view_factory()
    original = view.entries[0]
    current, snapshot = _updated(view)
    _queue(view, snapshot)
    collection = view.context.slots["0"].collection
    collection.remove(original.identity)

    _APP.processEvents()

    assert view.context.slots["0"].collection is collection
    assert len(collection) == 0
    assert _visible(view, "duplicate") == current


def test_invalidated_last_input_prevents_any_prepared_slot_from_being_published(view_factory):
    view = view_factory()
    original, other = view.entries
    current, snapshot = _updated(view)
    before = _snapshot((original, other))
    with ThreadPoolExecutor(max_workers=1) as pool:
        prepared = pool.submit(
            entry_projection.prepare_projected_entries, tuple(view.context.slots.values()), before, snapshot
        ).result(timeout=5)
    duplicate = view.context.slots["duplicate"]
    duplicate.collection = TranslationEntryCollection((replace(original, translation="最后slot的新编辑"),))

    assert prepared.apply() is None
    assert _visible(view) is original
    assert _visible(view, "duplicate").translation == "最后slot的新编辑"
    assert _visible(view).translation != current.translation


@pytest.mark.parametrize("local_edit_between_receipts", [False, True])
def test_second_queued_preparation_rebases_against_the_first_delivered_authority(
    view_factory, local_edit_between_receipts
):
    view = view_factory()
    first, first_snapshot = _updated(view, translation="first receipt", revision=1)
    latest, latest_snapshot = _updated(view, translation="latest receipt", revision=2)
    _queue(view, first_snapshot)
    _queue(view, latest_snapshot)

    def edit_after_first(_):
        if local_edit_between_receipts and _visible(view).translation == first.translation:
            view.context.slots["0"].collection = TranslationEntryCollection((
                replace(_visible(view), translation="两个回执之间的新编辑", stage=9),
            ))

    view.context.collection_changed.connect(edit_after_first)
    _APP.processEvents()

    if local_edit_between_receipts:
        assert (_visible(view).translation, _visible(view).stage) == ("两个回执之间的新编辑", 9)
        assert view.context.authoritative_projection_diverged()
    else:
        assert _visible(view) == latest
        assert not view.context.authoritative_projection_diverged()
    assert _visible(view, "duplicate") == latest
