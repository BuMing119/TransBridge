from __future__ import annotations

from dataclasses import replace

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
    RetryToken,
    SyncAction,
    SyncOperation,
    SyncPlanner,
)

NS = SourceNamespace("project:download-review")
SCOPE = f"paratranz:project:7:source:{NS.value}"


def _local(key="same", original="Original", translation="Local", *, ref=None, qualified=False, context=""):
    return LocalEntrySnapshot(
        EntryKey(NS, key, original if qualified else None),
        EntryRevision(1),
        original,
        translation,
        context,
        1,
        None if ref is None else ExternalEntryRef("paratranz", "project:7", ref),
    )


def _remote(key="same", original="Original", translation="Remote", *, ref=1, context=""):
    return RemoteEntrySnapshot(
        EntryKey(NS, key),
        f"revision-{ref}-{translation}",
        original,
        translation,
        context,
        1,
        None if ref is None else ExternalEntryRef("paratranz", "project:7", ref),
    )


def _plan(local, remote, decisions=(), **kwargs):
    return SyncPlanner(local_state_only=True).plan(
        local,
        remote,
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        scope=SCOPE,
        download_decisions=decisions,
        **kwargs,
    )


class _Remote:
    def __init__(self, entries):
        self.entries = entries

    def fetch(self, project_id, namespace, **kwargs):
        return self.entries


def _execute(plan, local, remote):
    official = list(local)
    service = _Remote(remote)
    executor = ParaTranzSyncExecutor(
        service,
        service,
        CallbackLocalSyncUnitOfWork(
            lambda: tuple(official), lambda entries: official.__setitem__(slice(None), entries)
        ),
    )
    result = executor.execute(
        ExecuteSyncRequest(
            AuthorizedSyncPlan(plan, "owner", "CONFIRMED"),
            7,
            NS,
            tuple(official),
            "download-run",
            ImmediateCommitGuard("download-run"),
        )
    )
    return result, official


def test_original_qualified_entries_download_their_own_remote_translation():
    local = (_local(original="First", qualified=True), _local(original="Second", qualified=True))
    remote = (_remote(original="Second", translation="二", ref=2), _remote(original="First", translation="一"))

    plan = _plan(local, remote)
    result, official = _execute(plan, local, remote)

    assert plan.conflicts == 0
    assert result.counts.succeeded == 2
    assert [(entry.original, entry.translation) for entry in official] == [("First", "一"), ("Second", "二")]
    assert [entry.entry_key for entry in official] == [entry.entry_key for entry in local]


def test_existing_reference_matches_a_renamed_remote_key():
    local = (_local(ref=2),)
    remote = (_remote(key="renamed", original="Updated original", ref=2), _remote(ref=1))

    plan = _plan(local, remote)
    result, official = _execute(plan, local, remote)

    assert result.counts.succeeded == 1
    assert official[0].external_ref.opaque_id == 2
    assert official[0].entry_key == local[0].entry_key


def test_context_can_disambiguate_equal_originals():
    local = (_local(context="dialogue"),)
    remote = (_remote(context="quest", ref=1), _remote(context="dialogue", ref=2))

    result, official = _execute(_plan(local, remote), local, remote)

    assert result.counts.succeeded == 1
    assert official[0].external_ref.opaque_id == 2


def test_partial_download_keeps_every_duplicate_local_entry_without_dict_loss():
    local = (
        _local(translation="First local"),
        _local(translation="Second local"),
        _local(key="safe", translation="Before"),
    )
    remote = (_remote(), _remote(key="safe", translation="After", ref=2))
    plan = _plan(local, remote)

    result, official = _execute(plan, local, remote)

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.counts.succeeded == 1
    assert result.counts.failed == 0
    assert result.value["deferred_count"] == 1
    assert official[:2] == list(local[:2])
    assert official[2].translation == "After"
    assert plan.download_review[0].local_occurrences == 2
    assert not plan.download_review[0].allow_remote_choice


def test_manual_candidate_is_exact_and_does_not_use_last_remote_key():
    local = (_local(),)
    remote = (_remote(translation="First", ref=1), _remote(translation="Second", ref=2))
    preview = _plan(local, remote)
    row = preview.download_review[0]
    selected = next(candidate for candidate in row.candidates if candidate.entry.external_ref.opaque_id == 1)
    decision = DownloadDecision(row.row_id, selected.token)

    resolved = _plan(local, remote, (decision,))
    result, official = _execute(resolved, local, tuple(reversed(remote)))

    assert resolved.conflicts == 0
    assert resolved.download_review[0].resolved
    assert resolved.plan_hash != preview.plan_hash
    assert result.counts.succeeded == 1
    assert official[0].translation == "First"
    assert official[0].external_ref.opaque_id == 1


def test_keep_local_resolves_conflict_without_changing_any_local_content():
    local = (_local(translation="First local"), _local(translation="Second local"))
    remote = (_remote(),)
    row = _plan(local, remote).download_review[0]

    plan = _plan(local, remote, (DownloadDecision(row.row_id, "keep_local"),))
    result, official = _execute(plan, local, remote)

    assert plan.conflicts == 0
    assert not plan.destructive
    assert plan.items[0].reason == "download_kept_local"
    assert result.counts.succeeded == 0
    assert result.value["deferred_count"] == 0
    assert official == list(local)


@pytest.mark.parametrize("changed", ["local", "remote", "candidates"])
def test_choices_cannot_be_replayed_after_the_reviewed_content_changes(changed):
    local = (_local(),)
    remote = (_remote(ref=1), _remote(translation="Alternative", ref=2))
    row = _plan(local, remote).download_review[0]
    decision = DownloadDecision(row.row_id, "keep_local")
    if changed == "local":
        local = (replace(local[0], translation="Edited locally"),)
    elif changed == "remote":
        remote = (replace(remote[0], translation="Edited remotely"), remote[1])
    else:
        remote = (*remote, _remote(ref=3))

    with pytest.raises(DownloadDecisionStaleError):
        _plan(local, remote, (decision,))


def test_unrelated_successful_update_does_not_expire_a_pending_conflict_choice():
    local = (_local(), _local(key="safe"))
    remote = (_remote(), _remote(ref=2), _remote(key="safe", ref=3))
    preview = _plan(local, remote)
    row = preview.download_review[0]
    result, updated = _execute(preview, local, remote)

    remaining = _plan(tuple(updated), remote, (DownloadDecision(row.row_id, "keep_local"),))

    assert result.counts.succeeded == 1
    assert remaining.conflicts == 0
    assert remaining.download_review[0].resolved


def test_remote_original_mismatch_requires_a_deliberate_choice():
    local = (_local(qualified=True),)
    remote = (_remote(original="Different source"),)

    preview = _plan(local, remote)

    assert preview.conflicts == 1
    row = preview.download_review[0]
    assert row.reason == "original_mismatch"
    resolved = _plan(local, remote, (DownloadDecision(row.row_id, row.candidates[0].token),))
    assert resolved.items[0].action is SyncAction.UPDATE_LOCAL


def test_unique_unqualified_key_keeps_legacy_matching_when_source_text_changes():
    local = (_local(),)
    remote = (_remote(original="Updated source", translation="Updated translation"),)

    plan = _plan(local, remote)

    assert plan.conflicts == 0
    assert plan.items[0].action is SyncAction.UPDATE_LOCAL


def test_remote_identity_linked_to_another_local_entry_is_never_stolen():
    local = (_local(key="linked", ref=1), _local())
    remote = (_remote(),)

    plan = _plan(local, remote)
    result, official = _execute(plan, local, remote)

    assert plan.conflicts == 1
    row = plan.download_review[0]
    assert row.reason == "remote_already_linked"
    assert not row.candidates[0].selectable
    assert not row.allow_remote_choice
    assert official[1] == local[1]
    assert result.counts.failed == 0


def test_shared_proposed_remote_identity_defers_both_targets():
    local = (_local(), _local(qualified=True))
    remote = (_remote(),)

    plan = _plan(local, remote)
    result, official = _execute(plan, local, remote)

    assert plan.conflicts == 2
    assert {row.reason for row in plan.download_review} == {"shared_remote_candidate"}
    assert result.value["deferred_count"] == 2
    assert official == list(local)


def test_duplicate_remote_ids_cannot_be_selected_manually():
    local = (_local(),)
    remote = (_remote(), _remote(key="other", ref=1))
    row = _plan(local, remote).download_review[0]

    assert not row.allow_remote_choice
    with pytest.raises(DownloadDecisionStaleError):
        _plan(local, remote, (DownloadDecision(row.row_id, row.candidates[0].token),))


def test_remote_only_ambiguity_is_skipped_without_an_unactionable_conflict():
    remote = (_remote(), _remote(translation="Alternative", ref=2))

    plan = SyncPlanner().plan((), remote, operation=SyncOperation.DOWNLOAD, scope=SCOPE)

    assert plan.conflicts == 0
    assert plan.items[0].action is SyncAction.SKIP
    assert plan.items[0].reason == "remote_only_ambiguous"
    assert not plan.download_review


def test_manual_selection_persists_identity_even_when_legacy_content_already_matches():
    local = (_local(),)
    remote = (_remote(translation="Local"), _remote(translation="Alternative", ref=2))
    planner = SyncPlanner()
    preview = planner.plan(
        local, remote, operation=SyncOperation.DOWNLOAD, conflict_policy=ConflictPolicy.PREFER_REMOTE, scope=SCOPE
    )
    row = preview.download_review[0]
    choice = next(candidate for candidate in row.candidates if candidate.entry.external_ref.opaque_id == 1)
    resolved = planner.plan(
        local,
        remote,
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        scope=SCOPE,
        download_decisions=(DownloadDecision(row.row_id, choice.token),),
    )

    result, official = _execute(resolved, local, remote)
    next_plan = planner.plan(
        tuple(official),
        remote,
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        scope=SCOPE,
    )

    assert result.counts.succeeded == 1
    assert official[0].external_ref.opaque_id == 1
    assert next_plan.conflicts == 0
    assert not next_plan.download_review


def test_retry_still_reports_preserved_unresolved_entries():
    local = (_local(), _local(key="safe"))
    remote = (_remote(), _remote(ref=2), _remote(key="safe", ref=3))
    plan = _plan(local, remote)
    first, updated = _execute(plan, local, remote)
    service = _Remote(remote)
    executor = ParaTranzSyncExecutor(
        service,
        service,
        CallbackLocalSyncUnitOfWork(lambda: tuple(updated), lambda entries: updated.__setitem__(slice(None), entries)),
    )

    retried = executor.execute(
        ExecuteSyncRequest(
            AuthorizedSyncPlan(plan, "owner", "CONFIRMED"),
            7,
            NS,
            tuple(updated),
            "download-run",
            ImmediateCommitGuard("download-run"),
            retry_token=RetryToken.from_dict(first.value["retry_token"]),
        )
    )

    assert retried.outcome is OperationOutcome.COMPLETED
    assert retried.value["deferred_count"] == 1
    assert retried.counts.succeeded == 0


def test_review_text_is_not_in_plan_projection_and_choices_are_order_independent():
    local = (_local(translation="Private local text"),)
    remote = (_remote(translation="Private remote text"), _remote(ref=2))
    plan = _plan(local, remote)
    reversed_plan = _plan(tuple(reversed(local)), tuple(reversed(remote)))

    assert plan.plan_hash == reversed_plan.plan_hash
    assert plan.download_review == reversed_plan.download_review
    assert "Private" not in repr(plan.to_dict())
    assert "Private" not in repr(plan)
