from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.application.io.publish import CommitDecision, ImmediateCommitGuard
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.sync import (
    AuthorizedSyncPlan,
    CallbackLocalSyncUnitOfWork,
    ConflictPolicy,
    ExecuteSyncRequest,
    LocalEntrySnapshot,
    ParaTranzSyncExecutor,
    RetryToken,
    SyncAction,
    SyncOperation,
    SyncPlanner,
    UploadDecision,
    UploadDecisionStaleError,
)
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter

NS = SourceNamespace("project:upload-review")
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


class Remote:
    def __init__(self, *entries):
        self.entries = {entry.remote_id: entry for entry in entries}
        self.calls = []
        self.fail = set()
        self.after_write = None

    def list_entries(self, project_id, **kwargs):
        return tuple(self.entries.values())

    def update_entry_translation(self, project_id, entry, **kwargs):
        self.calls.append(("update", entry.remote_id))
        if entry.remote_id in self.fail:
            raise ExternalServiceError(ExternalServiceCategory.UNAVAILABLE, "controlled failure")
        self.entries[entry.remote_id] = replace(
            self.entries[entry.remote_id], translation=entry.translation, stage=entry.stage
        )
        if self.after_write is not None:
            self.after_write()
        return self.entries[entry.remote_id]

    def upsert_entry(self, project_id, entry, **kwargs):
        assert entry.remote_id is None
        self.calls.append(("create", entry.key))
        if entry.key in self.fail:
            raise ExternalServiceError(ExternalServiceCategory.UNAVAILABLE, "controlled create failure")
        remote_id = max(self.entries, default=0) + 1
        self.entries[remote_id] = replace(entry, remote_id=remote_id)
        if self.after_write is not None:
            self.after_write()
        return self.entries[remote_id]

    def snapshots(self):
        return ParaTranzRemoteSnapshotAdapter(self).fetch(7, NS, limit=100)


def _remote(remote_id=1, key="same", original="Original", translation="Remote", context=""):
    return ParaTranzEntry(remote_id, key, original, translation, context, 1)


def _plan(local, remote, decisions=(), *, operation=SyncOperation.UPLOAD, scope=SCOPE):
    return SyncPlanner(local_state_only=True).plan(
        local,
        remote.snapshots(),
        operation=operation,
        conflict_policy=ConflictPolicy.PREFER_LOCAL
        if operation is SyncOperation.UPLOAD
        else ConflictPolicy.PREFER_REMOTE,
        scope=scope,
        upload_decisions=decisions,
    )


def _execute(plan, local, remote, *, retry=None, publish=None, guard=None, cancellation=None):
    executor = ParaTranzSyncExecutor(
        remote,
        ParaTranzRemoteSnapshotAdapter(remote),
        CallbackLocalSyncUnitOfWork(
            lambda: tuple(local), publish or (lambda value: local.__setitem__(slice(None), value))
        ),
    )
    return executor.execute(
        ExecuteSyncRequest(
            AuthorizedSyncPlan(plan, "owner", "CONFIRMED"),
            7,
            NS,
            tuple(local),
            "upload-run",
            guard or ImmediateCommitGuard("upload-run"),
            retry_token=retry,
            cancellation=cancellation,
        )
    )


def _select(plan, index=0, candidate=0):
    row = plan.upload_review[index]
    return UploadDecision(row.row_id, row.candidates[candidate].token)


def test_confirmed_reference_survives_renamed_key_and_preserves_all_remote_source_fields():
    local = [replace(_local(ref=42, qualified=True), context="local", context_order=12)]
    remote = Remote(_remote(42, key="renamed", original="Changed source", context="00000087|BOOK:FULL"))

    plan = _plan(local, remote)
    result = _execute(plan, local, remote)

    assert result.outcome is OperationOutcome.COMPLETED
    assert remote.calls == [("update", 42)]
    assert remote.entries[42] == _remote(
        42, key="renamed", original="Changed source", translation="Local", context="00000087|BOOK:FULL"
    )
    assert _plan(local, remote).items[0].action is SyncAction.SKIP


def test_original_qualified_roundtrip_uses_downloaded_id_then_uploads_only_local_translation():
    local = [_local(qualified=True, context="BOOK:FULL")]
    remote = Remote(_remote(context="00000087|BOOK:FULL"))
    assert _execute(_plan(local, remote, operation=SyncOperation.DOWNLOAD), local, remote).counts.succeeded == 1
    assert local[0].external_ref.opaque_id == 1
    local[0] = replace(local[0], translation="Edited locally", revision=local[0].revision.next(), context_order=3)
    remote.entries[1] = replace(remote.entries[1], key="renamed", original="Remote revision", context="remote context")

    result = _execute(_plan(local, remote), local, remote)

    assert result.outcome is OperationOutcome.COMPLETED
    assert remote.entries[1].translation == "Edited locally"
    assert (remote.entries[1].key, remote.entries[1].original, remote.entries[1].context) == (
        "renamed",
        "Remote revision",
        "remote context",
    )
    assert _plan(local, remote, operation=SyncOperation.DOWNLOAD).items[0].action is SyncAction.SKIP


def test_conflicting_original_is_deferred_while_safe_item_updates():
    local = [_local(), _local(key="safe", ref=2)]
    remote = Remote(_remote(original="Other"), _remote(2, key="safe"))
    plan = _plan(local, remote)
    result = _execute(plan, local, remote)
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value["deferred_count"] == 1
    assert remote.entries[1].translation == "Remote"
    assert remote.entries[2].translation == "Local"
    assert local[0].external_ref is None
    assert plan.upload_review[0].reason == "original_mismatch"


def test_manual_choice_of_duplicate_key_updates_exact_id_and_saves_local_association():
    local = [_local(original="Local source", qualified=True)]
    remote = Remote(_remote(1, original="A"), _remote(2, original="B"))
    initial = _plan(local, remote)
    choice = _select(initial, candidate=1)
    chosen = next(
        candidate.entry for candidate in initial.upload_review[0].candidates if candidate.token == choice.choice_token
    )
    result = _execute(_plan(local, remote, (choice,)), local, remote)
    assert result.outcome is OperationOutcome.COMPLETED
    assert local[0].external_ref == chosen.external_ref
    assert remote.calls == [("update", chosen.external_ref.opaque_id)]
    assert local[0].original == "Local source"
    assert _plan(local, remote).upload_review == ()


def test_manual_same_translation_choice_still_saves_association():
    local = [_local(original="Local source", translation="same", qualified=True)]
    remote = Remote(_remote(original="Remote source", translation="same"))
    plan = _plan(local, remote)
    result = _execute(_plan(local, remote, (_select(plan),)), local, remote)
    assert result.counts.succeeded == 1
    assert local[0].external_ref.opaque_id == 1
    assert _plan(local, remote).items[0].action is SyncAction.SKIP


@pytest.mark.parametrize("qualified", [False, True])
def test_stale_reference_does_not_rebind_or_recreate_without_explicit_choice(qualified):
    local = [_local(ref=77, qualified=qualified)]
    remote = Remote(_remote())
    plan = _plan(local, remote)
    assert plan.items[0].action is SyncAction.CONFLICT
    assert plan.upload_review[0].reason == "stale_remote_reference"
    _execute(plan, local, remote)
    assert remote.calls == []
    selected = _plan(local, remote, (_select(plan),))
    _execute(selected, local, remote)
    assert local[0].external_ref.opaque_id == 1


def test_qualified_local_only_has_keep_remote_choice_and_never_creates_bare_key():
    local = [_local(qualified=True)]
    remote = Remote()
    initial = _plan(local, remote)
    assert initial.upload_review[0].reason == "original_match_required"
    assert not initial.upload_review[0].allow_remote_choice
    kept = _plan(local, remote, (UploadDecision(initial.upload_review[0].row_id, "keep_remote"),))
    result = _execute(kept, local, remote)
    assert result.value["deferred_count"] == 0
    assert remote.calls == []
    assert local[0].external_ref is None


@pytest.mark.parametrize("change", ["local", "remote", "scope"])
def test_review_choice_is_bound_to_local_remote_and_scope(change):
    local = [_local(original="Other")]
    remote = Remote(_remote())
    choice = _select(_plan(local, remote))
    scope = SCOPE
    if change == "local":
        local[0] = replace(local[0], translation="changed")
    elif change == "remote":
        remote.entries[1] = replace(remote.entries[1], context="changed")
    else:
        scope = f"paratranz:project:8:source:{NS.value}"
    with pytest.raises(UploadDecisionStaleError):
        _plan(local, remote, (choice,), scope=scope)


def test_occupied_target_is_visible_but_unselectable():
    local = [_local(key="owner", ref=1), _local(original="Other")]
    remote = Remote(_remote())
    plan = _plan(local, remote)
    row = plan.upload_review[0]
    assert row.local.entry_key.local_key == "same"
    assert row.candidates[0].reason == "remote_already_linked"
    assert not row.candidates[0].selectable
    with pytest.raises(UploadDecisionStaleError):
        _plan(local, remote, (_select(plan),))


def test_two_local_identities_cannot_share_a_selected_remote_target():
    local = [_local(original="First", qualified=True), _local(original="Second", qualified=True)]
    remote = Remote(_remote())
    plan = _plan(local, remote)
    resolved = _plan(local, remote, (_select(plan, 0), _select(plan, 1)))
    assert resolved.conflicts == 2
    assert all(row.reason == "shared_remote_candidate" for row in resolved.upload_review)
    assert remote.calls == []


def test_partial_remote_failure_saves_only_successful_associations_and_retry_does_not_repeat_write():
    local = [_local(key="a"), _local(key="b")]
    remote = Remote(_remote(1, key="a"), _remote(2, key="b"))
    remote.fail.add(2)
    plan = _plan(local, remote)
    first = _execute(plan, local, remote)
    assert first.outcome is OperationOutcome.PARTIAL
    assert local[0].external_ref.opaque_id == 1
    assert local[1].external_ref is None
    remote.fail.clear()
    retry = RetryToken.from_dict(first.value["retry_token"])
    second = _execute(plan, local, remote, retry=retry)
    assert second.outcome is OperationOutcome.COMPLETED
    assert remote.calls == [("update", 1), ("update", 2), ("update", 2)]
    assert local[1].external_ref.opaque_id == 2


def test_failed_association_commit_reports_remote_success_and_retry_publishes_without_recreating():
    local = [_local()]
    remote = Remote()
    plan = _plan(local, remote)

    def fail_publish(entries):
        raise OSError("controlled local persistence failure")

    first = _execute(plan, local, remote, publish=fail_publish)
    assert first.outcome is OperationOutcome.PARTIAL
    assert first.value["association_pending"]
    assert first.diagnostics[0].code == "UPLOAD_ASSOCIATION_COMMIT_FAILED"
    assert first.counts.succeeded == 1
    assert local[0].external_ref is None
    second = _execute(plan, local, remote, retry=RetryToken.from_dict(first.value["retry_token"]))
    assert second.outcome is OperationOutcome.COMPLETED
    assert not second.value["association_pending"]
    assert local[0].external_ref.opaque_id == 1
    assert remote.calls == [("create", "same")]


def test_late_cancellation_keeps_success_checkpoint_and_does_not_bind_unexecuted_items():
    class Cancel:
        is_cancelled = False

        def raise_if_cancelled(self):
            if self.is_cancelled:
                raise RuntimeError("cancelled")

    class Reject:
        def commit(self, run_id, mutation):
            return CommitDecision(False, "cancelled")

    local = [_local(key="a"), _local(key="b")]
    remote = Remote()
    token = Cancel()
    remote.after_write = lambda: setattr(token, "is_cancelled", True)
    plan = _plan(local, remote)
    first = _execute(plan, local, remote, cancellation=token, guard=Reject())
    assert first.counts.succeeded == 1
    assert first.counts.cancelled == 1
    assert first.value["association_pending"]
    assert all(entry.external_ref is None for entry in local)
    remote.after_write = None
    second = _execute(plan, local, remote, retry=RetryToken.from_dict(first.value["retry_token"]))
    assert second.outcome is OperationOutcome.COMPLETED
    assert remote.calls == [("create", "a"), ("create", "b")]
    assert [entry.external_ref.opaque_id for entry in local] == [1, 2]


def test_retry_checks_renamed_target_by_id():
    local = [_local(key="a", ref=1), _local(key="b", ref=2)]
    remote = Remote(_remote(1, key="renamed-a"), _remote(2, key="renamed-b"))
    remote.fail.add(2)
    plan = _plan(local, remote)
    first = _execute(plan, local, remote)
    remote.fail.clear()
    second = _execute(plan, local, remote, retry=RetryToken.from_dict(first.value["retry_token"]))
    assert second.outcome is OperationOutcome.COMPLETED


def test_retry_rejects_new_creation_key_occupant():
    local = [_local(key="a"), _local(key="b")]
    remote = Remote()
    remote.fail.add("b")
    plan = _plan(local, remote)
    first = _execute(plan, local, remote)
    assert first.outcome is OperationOutcome.PARTIAL
    remote.fail.clear()
    remote.entries[9] = _remote(9, key="b", translation="Created by someone else")
    second = _execute(plan, local, remote, retry=RetryToken.from_dict(first.value["retry_token"]))
    assert second.outcome is OperationOutcome.FAILED
    assert second.diagnostics[0].code == "STALE_SYNC_PLAN"
    assert remote.calls == [("create", "a"), ("create", "b")]
    assert local[1].external_ref is None


def test_retry_does_not_recreate_any_successful_batch_member_after_association_failure():
    local = [_local(key="a"), _local(key="b")]
    remote = Remote()
    plan = _plan(local, remote)
    first = _execute(plan, local, remote, publish=lambda entries: (_ for _ in ()).throw(OSError("fail")))
    # All remote writes are checkpointed; retry must not issue any new create.
    assert len(remote.calls) == 2
    second = _execute(plan, local, remote, retry=RetryToken.from_dict(first.value["retry_token"]))
    assert second.outcome is OperationOutcome.COMPLETED
    assert len(remote.calls) == 2


def test_repeated_association_failure_returns_valid_receipt_and_preserves_retry_checkpoint():
    local = [_local()]
    remote = Remote()
    plan = _plan(local, remote)

    def fail_publish(entries):
        raise OSError("controlled local persistence failure")

    first = _execute(plan, local, remote, publish=fail_publish)
    second = _execute(plan, local, remote, publish=fail_publish, retry=RetryToken.from_dict(first.value["retry_token"]))
    assert second.outcome is OperationOutcome.PARTIAL
    assert second.counts.succeeded == 1
    assert second.counts.failed == 1
    assert second.value["association_pending"]
    assert len(RetryToken.from_dict(second.value["retry_token"]).confirmed_item_ids) == 1
    assert remote.calls == [("create", "same")]


def test_missing_linked_remote_never_creates_replacement():
    local = [_local(ref=77)]
    remote = Remote()
    plan = _plan(local, remote)
    assert plan.upload_review[0].reason == "stale_remote_reference"
    assert not plan.upload_review[0].allow_remote_choice
    assert _execute(plan, local, remote).value["deferred_count"] == 1
    assert remote.calls == []


def test_duplicate_remote_id_is_never_selectable_even_with_existing_association():
    local = [_local(ref=1)]
    remote = Remote(_remote())
    first = remote.snapshots()[0]
    duplicate = replace(first, entry_key=EntryKey(NS, "another-key"))
    plan = SyncPlanner().plan(
        local,
        (first, duplicate),
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
        scope=SCOPE,
    )
    assert plan.conflicts == 1
    assert plan.upload_review[0].reason == "duplicate_remote_id"
    assert all(not candidate.selectable for candidate in plan.upload_review[0].candidates)


def test_upload_plan_projection_does_not_include_review_translation_text():
    local = [_local(original="Local secret", translation="Sensitive local translation")]
    remote = Remote(_remote(original="Remote secret", translation="Sensitive remote translation"))
    plan = _plan(local, remote)
    assert plan.upload_review
    assert "Sensitive" not in repr(plan.to_dict())
    assert "secret" not in repr(plan.to_dict())


def test_original_qualified_siblings_match_distinct_originals_without_manual_review():
    local = [
        _local(original="First", translation="一", qualified=True),
        _local(original="Second", translation="二", qualified=True),
    ]
    remote = Remote(_remote(1, original="Second"), _remote(2, original="First"))
    plan = _plan(local, remote)
    assert plan.upload_review == ()
    assert _execute(plan, local, remote).counts.succeeded == 2
    assert remote.entries[1].translation == "二"
    assert remote.entries[2].translation == "一"
    assert [entry.external_ref.opaque_id for entry in local] == [2, 1]


def test_foreign_project_reference_never_establishes_an_id_match():
    local = [replace(_local(ref=1), external_ref=ExternalEntryRef("paratranz", "project:8", 1))]
    remote = Remote(_remote(original="Different source"))
    plan = _plan(local, remote)
    assert plan.upload_review[0].reason == "original_mismatch"
    assert _execute(plan, local, remote).value["deferred_count"] == 1
    assert remote.calls == []


def test_service_translation_update_sends_only_state_and_retains_order_metadata():
    strings = MagicMock()
    entry = _remote(context="00000428|BOOK:FULL", translation="new")
    strings.update_string.return_value = {"id": 1, **entry.to_remote_payload()}
    service = ParaTranzService(MagicMock(), strings, MagicMock(), MagicMock())
    result = service.update_entry_translation(7, entry)
    strings.update_string.assert_called_once_with(7, 1, {"translation": "new", "stage": 1}, cancellation=None)
    assert parse_ordered_context(result.context) == ("BOOK:FULL", 428)


@pytest.mark.parametrize("response_id", [None, 0, 2])
def test_service_rejects_wrong_update_identity(response_id):
    strings = MagicMock()
    entry = _remote()
    strings.update_string.return_value = {"id": response_id, **entry.to_remote_payload()}
    service = ParaTranzService(MagicMock(), strings, MagicMock(), MagicMock())
    with pytest.raises(ExternalServiceError):
        service.update_entry_translation(7, entry)


def test_uncertain_remote_response_never_claims_remote_was_unchanged():
    class UncertainRemote(Remote):
        def update_entry_translation(self, project_id, entry, **kwargs):
            super().update_entry_translation(project_id, entry, **kwargs)
            raise ExternalServiceError(
                ExternalServiceCategory.INVALID_RESPONSE, "response schema could not be confirmed"
            )

    local = [_local()]
    remote = UncertainRemote(_remote())
    result = _execute(_plan(local, remote), local, remote)
    assert result.outcome is OperationOutcome.FAILED
    assert "无法确认" in result.diagnostics[0].message
    assert "重新检查云端实际状态" in result.diagnostics[0].message
    assert remote.entries[1].translation == "Local"
    assert local[0].external_ref is None
