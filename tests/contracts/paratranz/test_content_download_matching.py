"""Project downloads reuse proven translation content, never remote ownership."""

from dataclasses import replace

import pytest

from tests.contracts.paratranz.download_result_support import merge_download
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.sync import candidate_matching, download_planner, local_candidate, planner
from transbridge.application.sync.batch_download import BatchDownloadSource, plan_batch_download
from transbridge.application.sync.download_review import DownloadDecision
from transbridge.application.sync.local_candidate import RemoteSourceIndex
from transbridge.application.sync.models import LocalEntrySnapshot, RemoteEntrySnapshot, SyncAction, SyncOperation
from transbridge.application.sync.planner import SyncPlanner

NS = SourceNamespace("source:content-download")


def local(key="current", *, original="Exact source", translation="", context="local context", stage=0):
    return LocalEntrySnapshot(
        EntryKey(NS, key), EntryRevision(3), original, translation, context, stage, context_order=17
    )


def remote(key="current", *, original="Exact source", translation="译文", context="cloud context", stage=1, ref=1):
    return RemoteEntrySnapshot(
        EntryKey(NS, key),
        f"remote-{ref}",
        original,
        translation,
        context,
        stage,
        ExternalEntryRef("paratranz", "project:7", ref),
        context_order=80,
    )


def source(locals_, remotes):
    return BatchDownloadSource("source-id", "Plugin.esp", tuple(locals_), tuple(remotes))


def applied(current, **kwargs):
    preview = plan_batch_download((current,), **kwargs)
    return preview, merge_download(preview, (current,))


def test_changed_key_falls_back_to_exact_original_and_materializes_without_id():
    current = source((local(),), (remote("old-layout-key"),))
    preview, candidate = applied(current)

    assert preview.deferred_count == 0
    assert preview.updated_count == 1
    assert preview.plans[0].items[0].reason == "download_original_match"
    updated = candidate.applied[0]
    assert (updated.entry_key, updated.original, updated.context) == (
        local().entry_key,
        local().original,
        local().context,
    )
    assert updated.translation == "译文"
    assert current.local_entries[0].context_order == 17


@pytest.mark.parametrize("stages,expected", [((5, 9), 1), ((-1, 0), 1), ((2, 3), 1), ((5, 5), 5)])
def test_same_translation_different_context_or_stage_is_content_consensus(stages, expected):
    current = source(
        (local(),),
        (remote(stage=stages[0]), remote(context="other context", stage=stages[1], ref=2)),
    )
    preview, candidate = applied(current)

    assert preview.deferred_count == 0
    assert preview.plans[0].download_review == ()
    assert preview.plans[0].items[0].reason == "download_translation_consensus"
    assert (candidate.applied[0].translation, candidate.applied[0].stage) == ("译文", expected)
    assert plan_batch_download((replace(current, remote_entries=tuple(reversed(current.remote_entries))),)).plans == (
        preview.plans[0],
    )


def test_stage_disagreement_retains_already_consistent_local_translation_state():
    current = source((local(translation="译文", stage=3),), (remote(stage=5), remote(stage=9, ref=2)))
    preview, candidate = applied(current)
    assert preview.updated_count == preview.deferred_count == 0
    assert candidate.applied == ()


def test_untranslated_copy_does_not_make_unique_nonempty_translation_ambiguous():
    current = source((local(),), (remote(), remote(translation="", stage=0, ref=2)))
    preview, candidate = applied(current)
    assert preview.deferred_count == 0
    assert candidate.applied[0].translation == "译文"


def test_one_remote_content_can_satisfy_multiple_same_source_local_keys():
    current = source((local("one"), local("two")), (remote("old"),))
    preview, candidate = applied(current)
    assert preview.deferred_count == 0 and preview.updated_count == 2
    assert [entry.translation for entry in candidate.applied] == ["译文", "译文"]


def test_real_translation_ambiguity_groups_metadata_variants_into_one_choice():
    current = source(
        (local(),),
        (remote("old-a"), remote("old-b", stage=9, ref=2), remote("old-c", translation="另译", ref=3)),
    )
    preview = plan_batch_download((current,))
    row = preview.plans[0].download_review[0]
    assert row.reason == "multiple_remote_translations"
    assert sorted(candidate.source_count for candidate in row.candidates) == [1, 2]
    chosen = next(candidate for candidate in row.candidates if candidate.entry.translation == "译文")
    accepted, candidate = applied(current, decisions=(DownloadDecision(row.row_id, chosen.token),))
    assert accepted.deferred_count == 0
    assert (candidate.applied[0].translation, candidate.applied[0].stage) == ("译文", 1)


@pytest.mark.parametrize("evidence", ["key_context", "key", "current_translation"])
def test_distinct_translations_can_be_resolved_by_deterministic_local_evidence(evidence):
    entry = local(translation="译文" if evidence == "current_translation" else "")
    preferred = remote("current" if evidence != "current_translation" else "old-a", context=entry.context)
    other = remote("current" if evidence == "key_context" else "old-b", translation="另译", ref=2)
    current = source((entry,), (preferred, other))
    preview, candidate = applied(current)
    assert preview.deferred_count == 0
    assert candidate.applied[0].translation == "译文"


@pytest.mark.parametrize("original", ["Other source", "exact source", "Exact source "])
def test_nonmatching_original_is_preserved_without_unusable_review(original):
    current = source((local(translation="我的本地译文", stage=3),), (remote(original=original),))
    preview, candidate = applied(current)
    assert preview.deferred_count == 0
    item = next(item for item in preview.plans[0].items if item.entry_key == local().entry_key)
    assert item.action is SyncAction.SKIP
    assert item.reason == "no_matching_original"
    assert candidate.applied == ()


def test_only_untranslated_remote_preserves_existing_local_translation():
    current = source((local(translation="本地译文", stage=5),), (remote(translation="", stage=0),))
    preview, candidate = applied(current)
    assert preview.deferred_count == 0
    assert preview.plans[0].items[0].reason == "remote_untranslated"
    assert candidate.applied == ()


def test_other_namespace_and_qualified_original_mismatch_cannot_supply_content():
    entry = local()
    foreign = replace(remote(), entry_key=EntryKey(SourceNamespace("source:other"), "current"))
    preview = SyncPlanner(content_download=True, local_state_only=True, preserve_local_source=True).plan(
        (entry,), (foreign,), operation=SyncOperation.DOWNLOAD
    )
    assert next(item for item in preview.items if item.entry_key == entry.entry_key).reason == "no_matching_original"

    invalid = replace(remote(), entry_key=EntryKey(NS, "current", "Claimed other original"))
    blocked = plan_batch_download((source((entry,), (invalid,)),))
    assert blocked.deferred_count == 1
    assert blocked.plans[0].download_review[0].reason == "original_mismatch"


def test_automatic_content_matching_never_bypasses_concurrent_local_edit_review():
    current = source((local(translation="刚改的本地译文", stage=1),), (remote("old"), remote("older", stage=5, ref=2)))
    preview = plan_batch_download((current,), review_keys=(local().entry_key,))
    row = preview.plans[0].download_review[0]
    assert row.reason == "local_content_changed"
    assert len(row.candidates) == 1
    kept, candidate = applied(
        current, review_keys=(local().entry_key,), decisions=(DownloadDecision(row.row_id, "keep_local"),)
    )
    assert kept.deferred_count == 0
    assert candidate.applied == ()
    accepted, candidate = applied(
        current, review_keys=(local().entry_key,), decisions=(DownloadDecision(row.row_id, row.candidates[0].token),)
    )
    assert accepted.deferred_count == 0
    assert candidate.applied[0].translation == "译文"


def test_existing_strict_download_contract_does_not_gain_general_original_fallback():
    preview = SyncPlanner(local_state_only=True).plan((local(),), (remote("old"),), operation=SyncOperation.DOWNLOAD)
    assert all(item.action is not SyncAction.UPDATE_LOCAL for item in preview.items)


@pytest.mark.parametrize("operation", [SyncOperation.UPLOAD, SyncOperation.BIDIRECTIONAL])
def test_opt_in_flag_does_not_change_upload_or_bidirectional_contracts(operation):
    args = ((local(),), (remote("old"),))
    assert SyncPlanner(content_download=True).plan(*args, operation=operation) == SyncPlanner().plan(
        *args, operation=operation
    )


@pytest.mark.parametrize("change", [{"translation": "云端改译"}, {"original": "Changed source"}, {"stage": 9}])
def test_content_materialization_rejects_changed_translation_original_or_stage(change):
    current = source((local(),), (remote("old"),))
    preview = plan_batch_download((current,))
    item = next(item for item in preview.plans[0].items if item.action is SyncAction.UPDATE_LOCAL)
    with pytest.raises(RuntimeError, match="reviewed remote translation content"):
        RemoteSourceIndex((replace(current.remote_entries[0], **change),)).get_for_item(item)


def test_content_materialization_cannot_use_another_sources_identical_text():
    current = source((local(),), (remote("old"),))
    preview = plan_batch_download((current,))
    item = next(item for item in preview.plans[0].items if item.action is SyncAction.UPDATE_LOCAL)
    foreign = replace(current.remote_entries[0], entry_key=EntryKey(SourceNamespace("source:foreign"), "old"))
    with pytest.raises(RuntimeError, match="reviewed remote translation content is unavailable"):
        RemoteSourceIndex((foreign,)).get_for_item(item)


def test_new_local_edit_review_omits_empty_remote_copies():
    current = source((local(),), (remote("old"), remote("older", translation="", ref=2)))
    preview = plan_batch_download((current,), review_keys=(local().entry_key,))
    row = preview.plans[0].download_review[0]
    assert row.reason == "local_content_changed"
    assert len(row.candidates) == 1
    assert row.candidates[0].entry.translation == "译文"


def test_invalid_local_qualified_identity_is_never_overwritten_by_content_matching():
    entry = replace(local(), entry_key=EntryKey(NS, "current", "Different identity source"))
    current = source((entry,), (remote(),))
    preview, candidate = applied(current)
    assert preview.deferred_count == 1
    assert preview.plans[0].download_review[0].reason == "original_mismatch"
    assert candidate.applied == ()


def test_empty_original_does_not_match_another_key_but_same_key_remains_usable():
    current = source(
        (local("one", original=""), local("two", original="")),
        (remote("one", original="", stage=5), remote("old", original="", translation="别的空文本译文", ref=2)),
    )
    preview, candidate = applied(current)
    assert preview.updated_count == 1 and preview.deferred_count == 0
    assert [(entry.translation, entry.stage) for entry in candidate.applied] == [("译文", 5)]
    assert candidate.applied[0].entry_key == current.local_entries[0].entry_key
    assert (
        next(item for item in preview.plans[0].items if item.entry_key.local_key == "two").reason
        == "no_matching_original"
    )


@pytest.mark.parametrize("key_evidence", [False, True])
def test_thousand_repeated_originals_are_indexed_once_and_fully_materialized(
    monkeypatch, record_property, key_evidence
):
    size = 1000
    current = source(
        tuple(local(f"current-{index}") for index in range(size)),
        tuple(
            remote(
                f"current-{index}" if key_evidence else f"old-{index}",
                translation="另译" if key_evidence and index % 2 else "译文",
                stage=5 if (index // 2) % 2 else 9,
                ref=index + 1,
            )
            for index in range(size)
        ),
    )
    counts = {"hash": 0, "identity": 0, "review": 0}
    real_hash = candidate_matching.canonical_hash
    real_identity = candidate_matching.consistent_original_identity
    real_review = download_planner._review

    def counted_hash(value):
        counts["hash"] += 1
        return real_hash(value)

    def counted_identity(value):
        counts["identity"] += 1
        return real_identity(value)

    def counted_review(*args, **kwargs):
        counts["review"] += 1
        return real_review(*args, **kwargs)

    for module in (candidate_matching, download_planner, planner):
        monkeypatch.setattr(module, "canonical_hash", counted_hash)
    for module in (candidate_matching, local_candidate):
        monkeypatch.setattr(module, "consistent_original_identity", counted_identity)
    monkeypatch.setattr(download_planner, "_review", counted_review)
    preview, candidate = applied(current)

    assert preview.updated_count == len(candidate.applied) == size
    assert preview.deferred_count == 0
    assert [entry.translation for entry in candidate.applied] == [
        "另译" if key_evidence and index % 2 else "译文" for index in range(size)
    ]
    assert all(entry.stage == 1 for entry in candidate.applied)
    assert counts["hash"] <= 9 * size + 20
    assert counts["identity"] <= 4 * size
    assert counts["review"] == 0
    for name, count in counts.items():
        record_property(f"{name}_calls", count)
