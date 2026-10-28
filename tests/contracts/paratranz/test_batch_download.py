from dataclasses import replace

import pytest

from tests.contracts.paratranz.download_result_support import merge_download
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.sync.batch_download import (
    BatchDownloadSource,
    plan_batch_download,
    snapshots_from_entries,
)
from transbridge.application.sync.download_review import DownloadDecision
from transbridge.application.sync.models import ConflictPolicy, LocalEntrySnapshot, RemoteEntrySnapshot
from transbridge.converter.translation_entry import TranslationEntry


def local(source="a", key="same", *, translation="Local", original="Source"):
    return LocalEntrySnapshot(
        EntryKey(SourceNamespace(f"source:{source}"), key), EntryRevision(1), original, translation
    )


def remote(entry, *, translation="Cloud", remote_id=1, context=""):
    return RemoteEntrySnapshot(
        entry.entry_key,
        f"remote-{remote_id}-{translation}",
        entry.original,
        translation,
        context,
        1,
        ExternalEntryRef("paratranz", "project:7", remote_id),
    )


def source(source_id="a", *, entries=None, remotes=None):
    entries = (local(source_id),) if entries is None else entries
    remotes = (remote(entries[0]),) if remotes is None else remotes
    return BatchDownloadSource(source_id, f"{source_id}.esp", entries, remotes)


def ambiguous(source_id="a", key="same"):
    entry = local(source_id, key)
    return source(
        source_id,
        entries=(entry,),
        remotes=(remote(entry), remote(entry, translation="Alternative", remote_id=2)),
    )


def test_same_key_across_sources_updates_only_its_own_translation():
    a = source("a")
    b_entry = local("b")
    b = source("b", entries=(b_entry,), remotes=(remote(b_entry, translation="Other cloud"),))
    preview = plan_batch_download((a, b))
    candidate = merge_download(preview, (a, b))

    assert preview.updated_count == 2
    assert [entry.translation for entry in candidate.applied] == ["Cloud", "Other cloud"]
    assert tuple(entry.entry_key for entry in candidate.applied) == (a.local_entries[0].entry_key, b_entry.entry_key)


def test_equivalent_duplicates_apply_without_choosing_or_persisting_an_id():
    entry = local()
    current = source(entries=(entry,), remotes=(remote(entry), remote(entry, remote_id=2)))
    preview = plan_batch_download((current,))
    candidate = merge_download(preview, (current,))

    assert preview.deferred_count == 0
    assert preview.updated_count == 1
    assert candidate.applied[0].translation == "Cloud"


def test_distinct_translations_require_review_while_other_source_continues():
    a, b = ambiguous(), source("b")
    preview = plan_batch_download((a, b))
    candidate = merge_download(preview, (a, b))

    assert preview.deferred_count == 1 and preview.updated_count == 1
    assert [entry.translation for entry in candidate.applied] == ["Cloud"]
    assert len(preview.plans[0].download_review[0].candidates) == 2


def test_concurrent_edit_requires_an_explicit_choice_while_other_items_can_apply():
    edited = local(key="edited", translation="New local edit")
    certain = local(key="certain")
    current = source(entries=(edited, certain), remotes=(remote(edited), remote(certain, remote_id=2)))
    preview = plan_batch_download((current,), review_keys=(edited.entry_key,))
    candidate = merge_download(preview, (current,))

    assert preview.updated_count == preview.deferred_count == 1
    row = preview.plans[0].download_review[0]
    assert row.reason == "local_content_changed"
    assert row.local.entry_key == edited.entry_key
    assert len(row.candidates) == 1 and row.allow_remote_choice
    assert [entry.translation for entry in candidate.applied] == ["Cloud"]
    assert tuple(entry.entry_key for entry in candidate.applied) == (certain.entry_key,)


@pytest.mark.parametrize("choice", ["keep_local", "remote"])
def test_explicit_concurrent_edit_choice_resolves_and_survives_replanning(choice):
    current = source()
    key = current.local_entries[0].entry_key
    preview = plan_batch_download((current,), review_keys=(key,))
    row = preview.plans[0].download_review[0]
    decision = DownloadDecision(row.row_id, "keep_local" if choice == "keep_local" else row.candidates[0].token)

    chosen = plan_batch_download((current,), decisions=(decision,), review_keys=(key,))
    candidate = merge_download(chosen, (current,))
    repeated = plan_batch_download((current,), decisions=(decision,), review_keys=(key,))

    assert chosen.deferred_count == 0
    assert chosen.updated_count == (0 if choice == "keep_local" else 1)
    assert [entry.translation for entry in candidate.applied] == ([] if choice == "keep_local" else ["Cloud"])
    assert chosen.plans[0].download_review[0].resolved
    assert chosen.cleared_decisions == repeated.cleared_decisions == ()
    assert repeated.plans[0].plan_hash == chosen.plans[0].plan_hash


def test_forced_review_drops_only_a_stale_choice_and_keeps_other_explicit_decisions():
    a, b = source(), source("b")
    keys = tuple(item.local_entries[0].entry_key for item in (a, b))
    preview = plan_batch_download((a, b), review_keys=keys)
    a_row, b_row = (plan.download_review[0] for plan in preview.plans)
    decisions = (
        DownloadDecision(a_row.row_id, "stale-token"),
        DownloadDecision(b_row.row_id, b_row.candidates[0].token),
    )
    chosen = plan_batch_download((a, b), decisions=decisions, review_keys=keys)

    assert chosen.cleared_decisions == (a_row.row_id,)
    assert chosen.deferred_count == chosen.updated_count == 1
    assert chosen.plans[1].download_review[0].resolved


def test_empty_forced_review_preserves_the_normal_automatic_download_contract():
    current = source()
    original = plan_batch_download((current,))
    explicit_empty = plan_batch_download((current,), review_keys=())

    assert original.plans == explicit_empty.plans
    assert original.updated_count == 1 and original.deferred_count == 0
    assert original.plans[0].download_review == ()


def test_review_ids_include_source_and_only_stale_decision_is_cleared():
    a, b = ambiguous(), ambiguous("b")
    preview = plan_batch_download((a, b))
    rows = [plan.download_review[0] for plan in preview.plans]
    assert rows[0].row_id != rows[1].row_id
    decisions = tuple(DownloadDecision(row.row_id, row.candidates[0].token) for row in rows)
    changed_a = replace(a, remote_entries=(replace(a.remote_entries[0], translation="New"), *a.remote_entries[1:]))
    refreshed = plan_batch_download((changed_a, b), decisions=decisions)

    assert refreshed.cleared_decisions == (rows[0].row_id,)
    assert refreshed.plans[1].download_review[0].resolved
    assert refreshed.deferred_count == 1 and refreshed.updated_count == 1


def test_removed_and_invalid_candidates_do_not_block_other_valid_review_choices():
    a, b = ambiguous(), ambiguous("b")
    preview = plan_batch_download((a, b))
    a_row, b_row = (plan.download_review[0] for plan in preview.plans)
    refreshed = plan_batch_download(
        (a, b),
        decisions=(
            DownloadDecision(a_row.row_id, "removed-candidate"),
            DownloadDecision(b_row.row_id, "keep_local"),
            DownloadDecision("missing-row", "keep_local"),
        ),
    )

    assert set(refreshed.cleared_decisions) == {a_row.row_id, "missing-row"}
    assert refreshed.plans[1].download_review[0].selected_choice == "keep_local"
    assert refreshed.plans[1].download_review[0].resolved


def test_local_policy_preserves_local_and_cloud_only_entries_are_never_added():
    current = source()
    outside = remote(local(key="cloud-only"), remote_id=2)
    current = replace(current, remote_entries=(*current.remote_entries, outside))
    preview = plan_batch_download((current,), policy=ConflictPolicy.PREFER_LOCAL)
    candidate = merge_download(preview, (current,))

    assert preview.updated_count == 0
    assert candidate.applied == ()
    assert tuple(entry.entry_key for entry in candidate.applied) == ()


def test_concurrent_target_edit_defers_only_target_and_preserves_other_edits():
    first, second, third = local(key="first"), local(key="second"), local(key="third", original="Unrelated source")
    current = source(entries=(first, second, third), remotes=(remote(first), remote(second, remote_id=2)))
    preview = plan_batch_download((current,))
    fresh = replace(
        current,
        local_entries=(
            replace(first, translation="User edit", revision=first.revision.next()),
            second,
            replace(third, translation="Unrelated edit", revision=third.revision.next()),
        ),
    )
    other = source("other")
    candidate = merge_download(preview, (fresh, other))

    assert [entry.translation for entry in candidate.applied] == ["Cloud"]
    assert candidate.pending[0].current.translation == "User edit"
    assert fresh.local_entries[2].translation == "Unrelated edit"
    assert tuple(entry.entry_key for entry in candidate.applied) == (second.entry_key,)
    assert tuple(row.entry_key for row in candidate.pending) == (first.entry_key,)


@pytest.mark.parametrize("change", ["original", "context", "namespace", "membership", "order"])
def test_source_changes_defer_only_affected_targets(change):
    first, second = local(key="first"), local(key="second")
    current = source(entries=(first, second), remotes=(remote(first),))
    preview = plan_batch_download((current,))
    if change == "namespace":
        fresh = replace(current, local_entries=(local("new", "first"),), remote_entries=())
    elif change == "membership":
        fresh = replace(current, local_entries=(first,))
    elif change == "order":
        fresh = replace(current, local_entries=(second, first))
    else:
        fresh = replace(current, local_entries=(replace(first, **{change: "Changed"}), second))
    candidate = merge_download(preview, (fresh,))

    if change == "namespace":
        assert candidate.applied == ()
        assert tuple(entry.entry_key for entry in candidate.applied) == ()
        assert set(tuple(row.entry_key for row in candidate.pending)) == {first.entry_key, second.entry_key}
    elif change in {"original", "context"}:
        assert tuple(entry.entry_key for entry in candidate.applied) == (second.entry_key,)
        assert tuple(row.entry_key for row in candidate.pending) == (first.entry_key,)
    elif change == "membership":
        assert tuple(entry.entry_key for entry in candidate.applied) == (first.entry_key,)
        assert tuple(row.entry_key for row in candidate.pending) == (second.entry_key,)
    else:
        assert set(tuple(entry.entry_key for entry in candidate.applied)) == {first.entry_key, second.entry_key}
        assert tuple(row.entry_key for row in candidate.pending) == ()


def test_missing_source_does_not_block_unchanged_remaining_source():
    a, b = source(), source("b")
    candidate = merge_download(plan_batch_download((a, b)), (b,))

    assert tuple(row.entry_key for row in candidate.pending) == (a.local_entries[0].entry_key,)
    assert tuple(entry.entry_key for entry in candidate.applied) == (b.local_entries[0].entry_key,)
    assert candidate.applied[0].translation == "Cloud"


def test_snapshot_capture_preserves_identity_order_context_and_ignores_old_cloud_refs():
    entries = tuple(
        TranslationEntry(
            key,
            key,
            "Source",
            "Local",
            3,
            "INFO:NAM1",
            entry_key=EntryKey(SourceNamespace("source:capture"), key, "Source"),
            external_refs=(ExternalEntryRef("paratranz", "project:7", 1),),
            revision=EntryRevision(4),
            metadata=(("plugin.source_order", order),),
        )
        for key, order in (("z", 9), ("a", 3))
    )
    captured = snapshots_from_entries(entries)

    assert [entry.entry_key for entry in captured] == [entry.identity for entry in entries]
    assert [entry.context_order for entry in captured] == [9, 3]
    assert all(entry.external_ref is None and entry.context == "INFO:NAM1" for entry in captured)
    assert all(entry.revision == EntryRevision(4) for entry in captured)


def test_invalid_cross_source_inputs_are_rejected_before_any_candidate_can_be_built():
    a = source()
    with pytest.raises(ValueError, match="another source"):
        replace(a, remote_entries=(remote(local("other")),))
    with pytest.raises(ValueError, match="overlapping"):
        plan_batch_download((a, replace(a, source_id="other")))
    with pytest.raises(ValueError, match="duplicate source"):
        plan_batch_download((a, a))
