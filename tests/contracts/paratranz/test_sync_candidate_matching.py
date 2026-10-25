from dataclasses import replace
from unittest.mock import patch

import pytest

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.sync import (
    AuthorizedSyncPlan,
    CallbackLocalSyncUnitOfWork,
    ConflictPolicy,
    DownloadDecision,
    DownloadDecisionStaleError,
    ExecuteSyncRequest,
    LocalEntrySnapshot,
    ParaTranzSyncExecutor,
    RemoteEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlanner,
    models,
)
from transbridge.application.sync.local_candidate import RemoteSourceIndex

NS = SourceNamespace("project:candidate-feedback")
SCOPE = f"paratranz:project:7:source:{NS.value}"
QUEST = "DLC2dunKolbjornQST:0201810B"


def local(key="same", original="Source"):
    return LocalEntrySnapshot(EntryKey(NS, key), EntryRevision(1), original, "Local", "context", 1)


def remote(key="same", original="Source", *, ref=1):
    return RemoteEntrySnapshot(
        EntryKey(NS, key),
        f"revision-{ref}",
        original,
        "Cloud",
        "context",
        1,
        ExternalEntryRef("paratranz", "project:7", ref),
    )


def plan(locals_, remotes, *, operation=SyncOperation.DOWNLOAD, decisions=()):
    return SyncPlanner(local_state_only=True).plan(
        locals_,
        remotes,
        operation=operation,
        scope=SCOPE,
        conflict_policy=ConflictPolicy.PREFER_REMOTE
        if operation is SyncOperation.DOWNLOAD
        else ConflictPolicy.PREFER_LOCAL,
        download_decisions=decisions,
    )


def execute(preview, locals_, remotes):
    official = list(locals_)

    class Remote:
        def fetch(self, *_args, **_kwargs):
            return remotes

    service = Remote()
    executor = ParaTranzSyncExecutor(
        service,
        service,
        CallbackLocalSyncUnitOfWork(lambda: tuple(official), lambda value: official.__setitem__(slice(None), value)),
    )
    result = executor.execute(
        ExecuteSyncRequest(
            AuthorizedSyncPlan(preview, "test", "CONFIRMED"),
            7,
            NS,
            tuple(locals_),
            "candidate-test",
            ImmediateCommitGuard("candidate-test"),
        )
    )
    return result, official


def test_identical_54_candidates_download_shared_translation_without_inventing_an_association():
    locals_ = (local(),)
    remotes = tuple(remote(ref=number) for number in range(1, 55))
    preview = plan(locals_, remotes)
    result, updated = execute(preview, locals_, tuple(reversed(remotes)))

    assert preview.conflicts == 0
    assert preview.download_review == ()
    assert preview.items[0].reason == "equivalent_remote_content"
    assert preview.plan_hash == plan(locals_, tuple(reversed(remotes))).plan_hash
    assert result.outcome is OperationOutcome.COMPLETED
    assert updated[0].translation == "Cloud"
    assert updated[0].external_ref is None
    assert plan(updated, remotes).items[0].action is SyncAction.SKIP

    edited = (replace(updated[0], translation="Local edit", revision=updated[0].revision.next()),)
    upload = plan(edited, remotes, operation=SyncOperation.UPLOAD)
    assert upload.conflicts == 1
    assert upload.upload_review[0].reason == "duplicate_remote_key"
    assert len(upload.upload_review[0].candidates) == 54
    assert all(item.action is not SyncAction.CREATE_REMOTE for item in upload.items)


@pytest.mark.parametrize(
    "change",
    [
        {"translation": "Other translation"},
        {"stage": 5},
        {"context": "Other context"},
    ],
)
def test_different_candidate_content_is_not_collapsed(change):
    # No local context preference, so both remote sources remain plausible.
    locals_ = (replace(local(), context="local context"),)
    remotes = (remote(), replace(remote(ref=2), **change))
    preview = plan(locals_, remotes)
    result, updated = execute(preview, locals_, remotes)

    assert preview.conflicts == 1
    assert result.value["deferred_count"] == 1
    assert updated == list(locals_)


def test_duplicate_ids_are_corrupt_identity_even_when_content_agrees():
    remotes = (remote(), replace(remote(), remote_revision="different version"))
    preview = plan((local(),), remotes)
    assert preview.download_review[0].reason == "duplicate_remote_id"


def test_duplicate_content_with_different_cloud_order_preserves_local_source_layout():
    locals_ = (replace(local(), context_order=88),)
    remotes = (replace(remote(), context_order=12), replace(remote(ref=2), context_order=90))
    result, updated = execute(plan(locals_, remotes), locals_, remotes)
    assert result.counts.succeeded == 1
    assert updated[0].context_order == 88
    assert updated[0].external_ref is None


def test_mixed_54_candidates_show_two_content_choices_and_bind_the_entire_group():
    locals_ = (local(),)
    remotes = tuple(remote(ref=number) for number in range(1, 54)) + (replace(remote(ref=54), stage=5),)
    preview = plan(locals_, remotes)
    row = preview.download_review[0]
    assert len(row.candidates) == 2
    group = next(candidate for candidate in row.candidates if candidate.source_count == 53)
    assert group.entry.external_ref is None
    decision = DownloadDecision(row.row_id, group.token)
    resolved = plan(locals_, remotes, decisions=(decision,))
    result, updated = execute(resolved, locals_, tuple(reversed(remotes)))

    assert result.counts.succeeded == 1
    assert updated[0].translation == "Cloud"
    assert updated[0].stage == 1
    assert updated[0].external_ref is None
    assert resolved.download_review[0].resolved
    assert resolved.plan_hash == plan(locals_, tuple(reversed(remotes)), decisions=(decision,)).plan_hash
    with pytest.raises(DownloadDecisionStaleError):
        plan(locals_, (replace(remotes[0], translation="Changed"), *remotes[1:]), decisions=(decision,))


def test_historical_local_reference_does_not_separate_a_content_group_in_review():
    locals_ = (replace(local(), external_ref=remote().external_ref),)
    remotes = tuple(remote(ref=number) for number in range(1, 54)) + (replace(remote(ref=54), stage=5),)
    preview = plan(locals_, remotes)
    assert preview.conflicts == 1
    assert sorted(candidate.source_count for candidate in preview.download_review[0].candidates) == [1, 53]


def test_changed_member_of_equivalent_group_invalidates_execution():
    locals_ = (local(),)
    remotes = (remote(), remote(ref=2))
    preview = plan(locals_, remotes)
    result, updated = execute(preview, locals_, (remotes[0], replace(remotes[1], translation="New cloud text")))

    assert result.outcome is OperationOutcome.FAILED
    assert updated == list(locals_)


def test_consensus_ignores_other_locals_historical_reference():
    locals_ = (local(), replace(local("owner"), external_ref=remote().external_ref))
    remotes = (remote(), remote(ref=2))
    preview = plan(locals_, remotes)
    assert preview.conflicts == 0
    result, updated = execute(preview, locals_, remotes)
    assert result.counts.succeeded == 1
    assert updated[0].translation == "Cloud"
    assert updated[1] == locals_[1]


@pytest.mark.parametrize("ref", [1, 99])
def test_historical_reference_does_not_split_or_block_equivalent_content(ref):
    locals_ = (replace(local(), external_ref=remote(ref=ref).external_ref),)
    remotes = (remote(), remote(ref=2))
    preview = plan(locals_, remotes)
    assert preview.conflicts == 0
    result, updated = execute(preview, locals_, remotes)
    assert result.counts.succeeded == 1
    assert updated[0].translation == "Cloud"
    assert updated[0].external_ref is None


def test_preserving_source_ignores_historical_id_when_translation_and_stage_are_current():
    current = replace(local(), external_ref=remote(ref=99).external_ref, translation="Cloud")
    cloud = replace(remote(), context="Cloud context", context_order=2)
    protected = SyncPlanner(preserve_local_source=True).plan(
        (current,),
        (cloud,),
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        scope=SCOPE,
    )
    assert protected.items[0].action is SyncAction.SKIP


@pytest.mark.parametrize("operation", [SyncOperation.DOWNLOAD, SyncOperation.UPLOAD])
def test_quest_journal_finds_exact_original_in_same_record_despite_shifted_ordinal(operation):
    locals_ = (local(f"{QUEST}|88~QUST:CNAM", "Exact journal text"),)
    remotes = (
        remote(f"{QUEST}|88~QUST:CNAM", "Different journal stage", ref=1),
        remote(f"{QUEST}|80~QUST:CNAM", "Exact journal text", ref=2),
    )
    preview = plan(locals_, remotes, operation=operation)

    assert preview.conflicts == 0
    selected = next(item for item in preview.items if item.entry_key == locals_[0].entry_key)
    assert selected.external_ref.opaque_id == 2
    if operation is SyncOperation.DOWNLOAD:
        result, updated = execute(preview, locals_, remotes)
        assert result.counts.succeeded == 1
        assert updated[0].entry_key == locals_[0].entry_key
        assert updated[0].external_ref is None
        assert updated[0].translation == "Cloud"


@pytest.mark.parametrize(
    "key,original",
    [
        ("OtherQuest:0201810B|80~QUST:CNAM", "Exact journal text"),
        (f"{QUEST.lower()}|80~QUST:CNAM", "Exact journal text"),
        (f"{QUEST}|80~QUST:NAM1", "Exact journal text"),
        (f"{QUEST}|80~QUST:CNAM", "Exact journal text "),
        (f"{QUEST}|80~QUST:CNAM", "exact journal text"),
    ],
)
def test_quest_match_does_not_cross_record_or_field_or_relax_original(key, original):
    locals_ = (local(f"{QUEST}|88~QUST:CNAM", "Exact journal text"),)
    remotes = (remote(f"{QUEST}|88~QUST:CNAM", "Different stage"), remote(key, original, ref=2))
    preview = plan(locals_, remotes)

    assert preview.conflicts == 1
    assert preview.download_review[0].reason == "original_mismatch"
    assert len(preview.download_review[0].candidates) == 1


def test_quest_exact_same_key_precedes_identical_text_at_other_ordinals():
    locals_ = (local(f"{QUEST}|88~QUST:CNAM"),)
    remotes = (remote(f"{QUEST}|88~QUST:CNAM"), remote(f"{QUEST}|80~QUST:CNAM", ref=2))
    preview = plan(locals_, remotes)
    selected = next(item for item in preview.items if item.entry_key == locals_[0].entry_key)

    assert preview.conflicts == 0
    assert selected.external_ref.opaque_id == 1


def test_several_exact_cross_ordinal_candidates_can_supply_content_but_not_identity():
    locals_ = (local(f"{QUEST}|88~QUST:CNAM"),)
    remotes = (
        remote(f"{QUEST}|88~QUST:CNAM", "Different stage"),
        remote(f"{QUEST}|80~QUST:CNAM", ref=2),
        remote(f"{QUEST}|81~QUST:CNAM", ref=3),
    )
    preview = plan(locals_, remotes)
    result, updated = execute(preview, locals_, remotes)

    assert preview.conflicts == 0
    assert result.counts.succeeded == 1
    assert updated[0].external_ref is None
    assert updated[0].original == "Source"
    upload = plan(updated, remotes, operation=SyncOperation.UPLOAD)
    assert upload.conflicts == 1
    assert len(upload.upload_review[0].candidates) == 3


@pytest.mark.parametrize("operation", [SyncOperation.DOWNLOAD, SyncOperation.UPLOAD])
def test_cross_ordinal_matching_does_not_claim_one_remote_id_for_two_local_entries(operation):
    locals_ = (local(f"{QUEST}|88~QUST:CNAM"), local(f"{QUEST}|89~QUST:CNAM"))
    preview = plan(locals_, (remote(f"{QUEST}|80~QUST:CNAM"),), operation=operation)
    assert preview.conflicts == 2
    reviews = preview.download_review if operation is SyncOperation.DOWNLOAD else preview.upload_review
    assert {row.reason for row in reviews} == {"shared_remote_candidate"}


def test_many_consensus_groups_in_one_quest_hash_remote_content_once():
    count = 200
    locals_ = tuple(local(f"{QUEST}|{1000 + index}~QUST:CNAM", f"Journal {index}") for index in range(count))
    remotes = tuple(
        remote(f"{QUEST}|{2 * index + offset}~QUST:CNAM", f"Journal {index}", ref=2 * index + offset + 1)
        for index in range(count)
        for offset in range(2)
    )
    preview = plan(locals_, remotes)
    assert len(preview.items) == count
    assert preview.conflicts == 0
    expected_originals = {entry.entry_key: entry.original for entry in locals_}

    with patch.object(models, "_text_hash", wraps=models._text_hash) as hash_text:
        sources = RemoteSourceIndex(remotes)
        assert hash_text.call_count == 3 * len(remotes)
        for _ in range(2):
            for item in preview.items:
                source = sources.get_for_item(item)
                assert source.external_ref is None
                assert source.entry_key == item.entry_key
                assert source.original == expected_originals[item.entry_key]
        assert hash_text.call_count == 3 * len(remotes)


@pytest.mark.parametrize("other_key", [f"{QUEST.lower()}|80~QUST:CNAM", QUEST])
def test_consensus_source_index_never_borrows_identical_content_from_other_record_scope(other_key):
    locals_ = (local(f"{QUEST}|88~QUST:CNAM"),)
    remotes = (remote(f"{QUEST}|80~QUST:CNAM"), remote(f"{QUEST}|81~QUST:CNAM", ref=2))
    item = plan(locals_, remotes).items[0]
    sources = RemoteSourceIndex((remotes[0], remote(other_key, ref=3)))
    with pytest.raises(RuntimeError, match="equivalent remote content is unavailable"):
        sources.get_for_item(item)
