"""Matching-original synchronization never overwrites drafts or partially applies conflicts."""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.dialogue_support import dialogue_entry
from tests.ui import test_dialogue_authority as authority_tests
from transbridge.application.io.identity import EntryRevision, ExternalEntryRef, Provenance
from transbridge.application.projections import ProjectionSnapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.dialogue.consistency import sync_candidates, synchronize_translation
from transbridge.ui.dialogue.editing import EntryDraft, commit_drafts, content_scope
from transbridge.ui.dialogue.sync_dialog import SyncTranslationDialog
from transbridge.ui.entry_projection import EntryProjectionUpdate
from transbridge.ui.source_hydration import apply_variant_projection

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def authority(monkeypatch):
    yield from authority_tests.authority.__wrapped__(monkeypatch)


@pytest.fixture
def context():
    entries = [
        replace(dialogue_entry(form=f"{index:08X}"), original="same", translation=text, stage=stage)
        for index, (text, stage) in enumerate((("new", 1), ("", 0), ("old", 5), ("draft", 1), ("locked", 9)))
    ]
    entries.append(replace(dialogue_entry(form="00000009"), original="same ", translation="", stage=0))
    notices = []
    return SimpleNamespace(
        active_version_identity=("project", "variant"),
        active_key="source",
        collection=TranslationEntryCollection(entries),
        uses_authoritative_projection=False,
        mark_dirty=lambda: notices.append("dirty"),
        collection_changed=SimpleNamespace(emit=lambda _: notices.append("changed")),
        notices=notices,
    )


def test_candidates_are_exact_scoped_and_protect_drafts_and_reviewed_defaults(context):
    entries = list(context.collection)
    pending = {(content_scope(context), entries[3].identity): EntryDraft.capture(context, entries[3])}
    candidates = sync_candidates(context, entries[0].identity, "new", pending)
    assert len(candidates) == 4
    assert [item.selected_by_default for item in candidates] == [True, False, False, False]
    dialog = SyncTranslationDialog(candidates, "same", "new")
    assert dialog.selected_candidates() == (candidates[0],)
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.selected_candidates() == candidates[:2]
    # Protected rows remain excluded even if their check state is changed programmatically.
    dialog.table.item(2, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.selected_candidates() == candidates[:2]
    dialog.deleteLater()


def test_selected_batch_is_atomic_and_preserves_review_stage(context):
    entries = list(context.collection)
    candidates = sync_candidates(context, entries[0].identity, "new", {})
    assert commit_drafts(context, (item.draft for item in candidates[:2])) is None
    assert context.collection.get(entries[1].identity).translation == "new"
    assert context.collection.get(entries[1].identity).stage == 1
    assert context.collection.get(entries[2].identity).stage == 5
    assert context.collection.get(entries[3].identity).translation == "draft"
    assert context.notices == ["dirty", "changed"]


def test_one_conflict_rejects_entire_batch(context):
    entries = list(context.collection)
    candidates = sync_candidates(context, entries[0].identity, "new", {})[:2]
    context.collection = TranslationEntryCollection(
        replace(entry, translation="external") if entry.identity == entries[2].identity else entry for entry in entries
    )
    assert "修改" in commit_drafts(context, (item.draft for item in candidates))
    assert context.collection.get(entries[1].identity).translation == ""
    assert not context.notices


def test_cancel_keeps_applied_source_and_does_not_mutate_candidates(context, monkeypatch):
    entries = list(context.collection)
    monkeypatch.setattr(SyncTranslationDialog, "exec", lambda _: 0)
    assert synchronize_translation(context, entries[0].identity, "new", {}, None) is None
    assert context.collection.get(entries[0].identity).translation == "new"
    assert context.collection.get(entries[1].identity).translation == ""
    assert not context.notices


@pytest.mark.parametrize("change", ["source", "variant", "draft"])
def test_changes_during_confirmation_abort_sync(context, monkeypatch, change):
    entries = list(context.collection)
    drafts = {}

    def accept(_dialog):
        if change == "source":
            context.collection = TranslationEntryCollection(
                replace(entry, translation="external") if entry.identity == entries[0].identity else entry
                for entry in entries
            )
        elif change == "variant":
            context.active_version_identity = ("project", "different")
        else:
            drafts[(content_scope(context), entries[1].identity)] = EntryDraft.capture(context, entries[1])
        return 1

    monkeypatch.setattr(SyncTranslationDialog, "exec", accept)
    assert synchronize_translation(context, entries[0].identity, "new", drafts, None)
    assert context.collection.get(entries[1].identity).translation == ""
    assert not context.notices


def test_real_authority_commits_batch_once_preserving_unselected_state(authority):
    context, store, aggregate, _, _, other_key = authority
    entries = list(context.collection)[:2]
    drafts = [EntryDraft.capture(context, entry) for entry in entries]
    for draft in drafts:
        draft.text = "统一"
    assert commit_drafts(context, drafts, projection=store) is None
    assert aggregate.snapshot().revision == 1
    states = {entry.entry_key: entry for entry in aggregate.snapshot().entries}
    assert all(states[entry.identity].translation == "统一" for entry in entries)
    assert states[other_key].translation == "其他来源"
    assert all(context.collection.get(entry.identity).translation == "统一" for entry in entries)


def test_real_authority_conflict_rejects_all_selected_entries(authority):
    context, store, aggregate, _, _, _ = authority
    entries = list(context.collection)[:2]
    drafts = [EntryDraft.capture(context, entry) for entry in entries]
    for draft in drafts:
        draft.text = "统一"
    assert context.project_commands.replace_entry_states(
        {entries[1].identity: ("external", 1)}, context.runtime_context
    ).is_success
    assert "工程中的词条已变化" in commit_drafts(context, drafts, projection=store)
    assert aggregate.snapshot().revision == 1
    states = {entry.entry_key: entry for entry in aggregate.snapshot().entries}
    assert states[entries[0].identity].translation == ""
    assert states[entries[1].identity].translation == "external"


def test_commit_refreshes_external_slot_changes_and_reuses_unchanged_entries(authority):
    context, store, _, _, target, other_key = authority
    other = replace(target, key=other_key.local_key, entry_key=other_key, translation="其他来源", stage=1)
    context.slots["other"] = context_module.CollectionSlot("Other", TranslationEntryCollection((other,)))
    unchanged = replace(dialogue_entry(form="FFFFFFFF"), translation="not in projection")
    stable_collection = TranslationEntryCollection((unchanged,))
    context.slots["stable"] = context_module.CollectionSlot("Stable", stable_collection)
    stable_entry = next(entry for entry in context.collection if entry.identity != target.identity)
    # A different writer can change another source before this editor applies its own draft.
    assert context.project_commands.replace_entry_states(
        {other_key: ("外部新译文", 3)}, context.runtime_context
    ).is_success
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "当前改动"
    assert draft.commit(context, projection=store) is None
    assert context.slots["other"].collection.get(other_key).translation == "外部新译文"
    assert context.slots["other"].collection.get(other_key).stage == 3
    assert context.slots["stable"].collection is stable_collection
    assert context.collection.get(stable_entry.identity) is stable_entry
    # Applying the same authoritative snapshot a second time preserves every collection.
    updater = EntryProjectionUpdate.from_snapshot(store.snapshot())
    for slot in context.slots.values():
        assert updater.collection(slot.collection) is slot.collection


def test_projection_update_matches_canonical_revision_provenance_and_refs(monkeypatch):
    entry = dialogue_entry()
    ref = ExternalEntryRef.from_dict({
        "system": "paratranz",
        "scope": "42",
        "opaque_id": "7",
        "metadata": {"nested": {"array": [1, 2]}},
    })
    provenance = Provenance("run", "writer", "test", metadata=(("nested", {"array": [3]}),))
    state = {
        "entry_key": entry.identity.to_dict(),
        "translation": "译文",
        "stage": 5,
        "revision": 8,
        "external_refs": [ref.to_dict()],
        "provenance": [provenance.to_dict()],
    }
    snapshot = ProjectionSnapshot("test", 1, 0, {"entries": [state]})
    monkeypatch.setattr(ProjectionSnapshot, "to_dict", lambda *_: pytest.fail("Whole snapshot copied for a UI read"))
    collection = TranslationEntryCollection((entry,))
    projected = EntryProjectionUpdate.from_snapshot(snapshot).collection(collection).get(entry.identity)
    canonical = apply_variant_projection(collection, [state]).get(entry.identity)
    assert projected.snapshot() == canonical.snapshot()
    assert projected.revision == EntryRevision(8)
    assert projected.external_refs == (ref,)
    assert projected.provenance == (provenance,)
    state["inferred_fields"] = ["external_refs"]
    snapshot = ProjectionSnapshot("test", 2, 0, {"entries": [state]})
    assert EntryProjectionUpdate.from_snapshot(snapshot).entry(entry).external_refs == entry.external_refs


def test_missing_authoritative_snapshot_cannot_commit(authority):
    context, _, aggregate, _, target, _ = authority
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "保留"
    assert "快照不可用" in draft.commit(context, projection=SimpleNamespace(snapshot=lambda: None))
    assert aggregate.snapshot().revision == 0


def test_public_projection_preserves_fresh_collection_and_matching_entry_contract():
    entry = dialogue_entry()
    collection = TranslationEntryCollection((entry,))
    state = {"entry_key": entry.identity.to_dict(), "translation": entry.translation, "stage": entry.stage}
    projected = apply_variant_projection(collection, [state])
    assert projected is not collection
    assert projected.get(entry.identity) is not entry
    assert projected.get(entry.identity).snapshot() == entry.snapshot()
    assert EntryProjectionUpdate([state]).collection(collection) is collection
    unmatched = apply_variant_projection(collection, [])
    assert unmatched is not collection and unmatched.get(entry.identity) is entry


def test_missing_snapshot_after_commit_reports_applied_but_refresh_failed(authority):
    context, store, aggregate, _, target, _ = authority
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "已提交"
    snapshots = iter((store.snapshot(), None))
    error = draft.commit(context, projection=SimpleNamespace(snapshot=lambda: next(snapshots)))
    assert "已提交" in error and "快照不可用" in error
    states = {entry.entry_key: entry for entry in aggregate.snapshot().entries}
    assert states[target.identity].translation == "已提交"
