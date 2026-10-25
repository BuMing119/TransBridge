"""Authority content advances clean views while genuine UI edits stay visible."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from tests.conftest import make_entry
from transbridge.application.io.identity import EntryRevision, ExternalEntryRef, Provenance
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import AppContext, CollectionSlot

_APP = QApplication.instance() or QApplication([])


def _snapshot(entries, revision=0, *, project="project", variant="variant", stream=None):
    return ProjectionSnapshot(
        stream or f"project:{project}",
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
                    "provenance": [provenance.to_dict() for provenance in entry.provenance],
                }
                for entry in entries
            ],
        },
    )


def _ref(remote_id):
    return ExternalEntryRef("paratranz", "project:42", remote_id)


@pytest.fixture
def view_factory(monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    contexts = []

    def create(entries=None):
        entries = entries or (make_entry("one", translation="旧译文", stage=3), make_entry("other"))
        store = ProjectionStore(_snapshot(entries))
        context = AppContext(project_projection=store)
        contexts.append(context)
        for index, entry in enumerate(entries):
            context.add_slot(str(index), CollectionSlot(str(index), TranslationEntryCollection((entry,))))
        context.add_slot("duplicate", CollectionSlot("duplicate", TranslationEntryCollection((entries[0],))))
        context.activate_slot("0")
        return SimpleNamespace(context=context, store=store, entries=entries)

    yield create
    for context in contexts:
        context.close_projection()
    _APP.processEvents()


def _visible(view, slot="0"):
    return view.context.slots[slot].collection.get(view.entries[0].identity)


def test_saved_content_without_reference_change_reconciles_all_loaded_copies_before_notification(view_factory):
    view = view_factory()
    original, other = view.entries
    untouched = view.context.slots["1"].collection
    observations = []
    view.context.collection_changed.connect(
        lambda _: observations.append(view.context.authoritative_projection_diverged())
    )
    current = replace(original, translation="已保存云端译文", stage=5, revision=EntryRevision(1))

    view.store.rebuild(_snapshot((current, other), 1))

    assert observations == [False]
    assert _visible(view) == _visible(view, "duplicate") == current
    assert view.context.slots["1"].collection is untouched
    view.store.rebuild(_snapshot((current, other), 1))
    assert observations == [False]


@pytest.mark.parametrize(
    "edit", [{"translation": "真正未提交译文"}, {"stage": 9}, {"translation": "新译文", "stage": 1}]
)
def test_translation_and_stage_are_an_atomic_three_way_group_and_real_ui_edits_survive(view_factory, edit):
    view = view_factory()
    original, other = view.entries
    local = replace(original, **edit, revision=EntryRevision(8))
    view.context.slots["0"].collection = TranslationEntryCollection((local,))
    current = replace(
        original, translation="云端保存译文", stage=5, external_refs=(_ref(101),), revision=EntryRevision(2)
    )

    view.store.rebuild(_snapshot((current, other), 2))

    visible = _visible(view)
    assert (visible.translation, visible.stage) == (local.translation, local.stage)
    assert visible.external_refs == current.external_refs
    assert visible.revision.value == 8
    assert _visible(view, "duplicate") == current
    assert view.context.authoritative_projection_diverged()


def test_reference_edit_does_not_block_independent_authoritative_content_update(view_factory):
    view = view_factory()
    original, other = view.entries
    local = replace(original, external_refs=(_ref(999),))
    view.context.slots["0"].collection = TranslationEntryCollection((local,))
    current = replace(original, translation="新译文", stage=5, external_refs=(_ref(101),), revision=EntryRevision(2))

    view.store.rebuild(_snapshot((current, other), 2))

    visible = _visible(view)
    assert (visible.translation, visible.stage) == (current.translation, current.stage)
    assert visible.external_refs == local.external_refs
    assert view.context.authoritative_projection_diverged()


def test_already_projected_current_content_can_acknowledge_revision_and_provenance(view_factory):
    view = view_factory()
    original, other = view.entries
    current = replace(
        original,
        translation="已提前投影的译文",
        stage=5,
        revision=EntryRevision(4),
        provenance=(Provenance("download-run", "user", "paratranz"),),
    )
    view.context.slots["0"].collection = TranslationEntryCollection((
        replace(current, revision=original.revision, provenance=()),
    ))

    view.store.rebuild(_snapshot((current, other), 4))

    assert _visible(view) == current
    assert not view.context.authoritative_projection_diverged()


def test_unsubmitted_provenance_is_preserved_while_clean_content_advances(view_factory):
    view = view_factory()
    original, other = view.entries
    local = replace(original, provenance=(Provenance("ui-run", "user", "inline"),))
    view.context.slots["0"].collection = TranslationEntryCollection((local,))
    current = replace(
        original,
        translation="已保存译文",
        stage=1,
        revision=EntryRevision(2),
        provenance=(Provenance("cloud-run", "user", "paratranz"),),
    )

    view.store.rebuild(_snapshot((current, other), 2))

    assert (_visible(view).translation, _visible(view).stage) == (current.translation, current.stage)
    assert _visible(view).provenance == local.provenance


@pytest.mark.parametrize("missing", ["translation", "stage"])
@pytest.mark.parametrize("side", ["previous", "current"])
def test_incomplete_content_states_never_invent_an_authoritative_clear(view_factory, missing, side):
    view = view_factory()
    original, other = view.entries
    current = replace(original, translation="新译文", stage=5, external_refs=(_ref(101),), revision=EntryRevision(1))
    previous_snapshot = _snapshot((original, other))
    current_snapshot = _snapshot((current, other), 1)
    target = previous_snapshot if side == "previous" else current_snapshot
    values = target.to_dict()["values"]
    values["entries"][0].pop(missing)
    incomplete = ProjectionSnapshot(target.stream_id, target.revision, target.persisted_revision, values)
    if side == "previous":
        view.store.rebuild(incomplete)
    else:
        current_snapshot = incomplete

    view.store.rebuild(current_snapshot)

    visible = _visible(view)
    assert (visible.translation, visible.stage) == (original.translation, original.stage)
    assert visible.external_refs == current.external_refs


@pytest.mark.parametrize("field", ["translation", "stage"])
def test_inferred_content_is_not_treated_as_explicit_authority(view_factory, field):
    view = view_factory()
    original, other = view.entries
    current = replace(original, translation="推断内容", stage=0, external_refs=(_ref(101),), revision=EntryRevision(1))
    snapshot = _snapshot((current, other), 1)
    values = snapshot.to_dict()["values"]
    values["entries"][0]["inferred_fields"] = [field]

    view.store.rebuild(ProjectionSnapshot(snapshot.stream_id, snapshot.revision, snapshot.persisted_revision, values))

    visible = _visible(view)
    assert (visible.translation, visible.stage) == (original.translation, original.stage)
    assert visible.external_refs == current.external_refs


@pytest.mark.parametrize("target", [{"project": "another"}, {"variant": "another"}, {"stream": "another-stream"}])
def test_another_project_variant_or_stream_cannot_rewrite_installed_content(view_factory, target):
    view = view_factory()
    original, other = view.entries
    collection = view.context.collection
    current = replace(original, translation="其他版本的内容", stage=9, revision=EntryRevision(1))

    view.store.rebuild(_snapshot((current, other), 1, **target))

    assert view.context.collection is collection
    assert _visible(view) is original


def test_reordered_same_key_original_qualified_entries_are_merged_by_complete_identity(view_factory):
    first, second = (
        TranslationEntry("same", "same", original, "旧译文", 3, "QUST:CNAM", requires_original_match=True)
        for original in ("first exact original", "second exact original")
    )
    view = view_factory((first, second))
    current = replace(first, translation="first-only", stage=5, revision=EntryRevision(1))

    view.store.rebuild(_snapshot((second, current), 1))

    assert _visible(view) == current
    assert view.context.slots["1"].collection.get(second.identity) is second
    assert not view.context.authoritative_projection_diverged()


@pytest.mark.parametrize("local_edit", [False, True])
def test_queued_authority_content_uses_successive_baselines_and_does_not_lose_local_edits(view_factory, local_edit):
    view = view_factory()
    original, other = view.entries
    first = replace(original, translation="first receipt", stage=1, revision=EntryRevision(1))
    latest = replace(original, translation="second receipt", stage=5, revision=EntryRevision(2))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(view.store.rebuild, _snapshot((first, other), 1)).result(timeout=5)
        pool.submit(view.store.rebuild, _snapshot((latest, other), 2)).result(timeout=5)
    assert _visible(view) is original
    local = replace(original, translation="等待期间真正新编辑", stage=9)
    if local_edit:
        view.context.slots["0"].collection = TranslationEntryCollection((local,))

    _APP.processEvents()
    view.context._project_view._deliver(2, _snapshot((first, other), 1))

    expected = local if local_edit else latest
    assert (_visible(view).translation, _visible(view).stage) == (expected.translation, expected.stage)
    assert _visible(view, "duplicate") == latest
    assert view.context.authoritative_projection_diverged() is local_edit
