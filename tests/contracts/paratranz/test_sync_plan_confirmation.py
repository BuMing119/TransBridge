from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from transbridge.application.io.identity import (
    EntryKey,
    EntryRevision,
    ExternalEntryRef,
    SourceNamespace,
)
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.security.hitl import ConfirmationAuthority
from transbridge.application.sync import (
    AuthorizeSyncPlanRequest,
    ConflictPolicy,
    CreateSyncPlanRequest,
    DeletionPolicy,
    LocalEntrySnapshot,
    ParaTranzSyncPlanningUseCase,
    RemoteEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlanAuthorizationError,
    SyncPlanner,
    SyncPlanStaleError,
)
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter

NS = SourceNamespace("project:skyrim:main")


def _key(value: str) -> EntryKey:
    return EntryKey(NS, value)


def _ref(value: int | None, *, key_scope: str = "project:7") -> ExternalEntryRef | None:
    return None if value is None else ExternalEntryRef("paratranz", key_scope, value)


def _local(
    key: str,
    translation: str,
    *,
    revision: int = 1,
    deleted: bool = False,
    remote_id: int | None = None,
) -> LocalEntrySnapshot:
    return LocalEntrySnapshot(
        _key(key),
        EntryRevision(revision),
        f"original-{key}",
        translation,
        "context",
        1,
        _ref(remote_id),
        deleted,
    )


def _remote(
    key: str,
    translation: str,
    *,
    revision: str = "remote-1",
    deleted: bool = False,
    remote_id: int | None = 1,
) -> RemoteEntrySnapshot:
    return RemoteEntrySnapshot(
        _key(key),
        revision,
        f"original-{key}",
        translation,
        "context",
        1,
        _ref(remote_id),
        deleted,
    )


def test_golden_plan_covers_create_update_conflict_skip_and_explicit_delete() -> None:
    local = (
        _local("create", "local"),
        _local("update", "local", remote_id=2),
        _local("same", "same", remote_id=3),
        _local("delete", "", deleted=True, remote_id=4),
    )
    remote = (
        _remote("update", "remote", remote_id=2),
        _remote("same", "same", remote_id=3),
        _remote("delete", "remote", remote_id=4),
        _remote("remote-only", "remote", remote_id=5),
    )

    preferred = SyncPlanner().plan(
        local,
        remote,
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
    )
    actions = {item.entry_key.local_key: item.action for item in preferred.items}

    assert actions == {
        "create": SyncAction.CREATE_REMOTE,
        "delete": SyncAction.DELETE_REMOTE,
        "remote-only": SyncAction.SKIP,
        "same": SyncAction.SKIP,
        "update": SyncAction.UPDATE_REMOTE,
    }
    assert preferred.destructive
    assert preferred.requires_confirmation
    assert dict(preferred.counts)["update_remote"] == 1
    assert dict(preferred.counts)["delete_remote"] == 1

    conflicts = SyncPlanner().plan(
        local,
        remote,
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.ABORT,
    )
    assert {item.entry_key.local_key for item in conflicts.items if item.action is SyncAction.CONFLICT} == {"update"}


@pytest.mark.parametrize(
    ("operation", "policy", "expected"),
    [
        (SyncOperation.UPLOAD, ConflictPolicy.PREFER_LOCAL, SyncAction.UPDATE_REMOTE),
        (SyncOperation.DOWNLOAD, ConflictPolicy.PREFER_REMOTE, SyncAction.UPDATE_LOCAL),
        (SyncOperation.BIDIRECTIONAL, ConflictPolicy.PREFER_LOCAL, SyncAction.UPDATE_REMOTE),
        (SyncOperation.BIDIRECTIONAL, ConflictPolicy.PREFER_REMOTE, SyncAction.UPDATE_LOCAL),
        (SyncOperation.BIDIRECTIONAL, ConflictPolicy.SKIP, SyncAction.SKIP),
        (SyncOperation.BIDIRECTIONAL, ConflictPolicy.ABORT, SyncAction.CONFLICT),
    ],
)
def test_operation_and_conflict_policy_are_explicit(operation, policy, expected) -> None:
    plan = SyncPlanner().plan(
        (_local("key", "local", remote_id=1),),
        (_remote("key", "remote", remote_id=1),),
        operation=operation,
        conflict_policy=policy,
    )

    assert plan.items[0].action is expected
    assert plan.items[0].conflict_policy is policy


def test_download_remote_priority_turns_content_difference_into_local_update() -> None:
    plan = SyncPlanner().plan(
        (_local("key", "local", remote_id=1),),
        (_remote("key", "remote", remote_id=1),),
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        deletion_policy=DeletionPolicy.PRESERVE,
    )

    assert plan.conflicts == 0
    assert plan.items[0].action is SyncAction.UPDATE_LOCAL


def test_download_preserves_remote_deletions_unless_user_opts_in() -> None:
    local = (_local("key", "local", remote_id=1),)
    remote = (_remote("key", "", remote_id=1, deleted=True),)

    protected = SyncPlanner().plan(
        local,
        remote,
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        deletion_policy=DeletionPolicy.PRESERVE,
    )
    applying = SyncPlanner().plan(
        local,
        remote,
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        deletion_policy=DeletionPolicy.APPLY,
    )

    assert protected.items[0].action is SyncAction.SKIP
    assert protected.items[0].reason == "remote_tombstone_preserved"
    assert applying.items[0].action is SyncAction.DELETE_LOCAL


def test_download_8300_entry_regression_reports_8096_updates_instead_of_conflicts() -> None:
    local = tuple(_local(f"key-{index}", "same", remote_id=index + 1) for index in range(8300))
    remote = tuple(
        _remote(
            f"key-{index}",
            "remote" if index < 8096 else "same",
            remote_id=index + 1,
        )
        for index in range(8300)
    )

    plan = SyncPlanner().plan(
        local,
        remote,
        operation=SyncOperation.DOWNLOAD,
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
        deletion_policy=DeletionPolicy.PRESERVE,
    )

    assert plan.conflicts == 0
    assert dict(plan.counts)["update_local"] == 8096
    assert dict(plan.counts)["skip"] == 204


def test_missing_and_duplicate_remote_ids_are_inspectable_conflicts() -> None:
    missing = SyncPlanner().plan(
        (_local("a", "local"),),
        (_remote("a", "remote", remote_id=None),),
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
    )
    duplicated = SyncPlanner().plan(
        (_local("a", "local-a"), _local("b", "local-b")),
        (
            _remote("a", "remote-a", remote_id=9),
            _remote("b", "remote-b", remote_id=9),
        ),
        operation=SyncOperation.BIDIRECTIONAL,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
    )

    assert missing.items[0].action is SyncAction.CONFLICT
    assert missing.items[0].reason == "remote_id_missing"
    assert all(item.action is SyncAction.CONFLICT for item in duplicated.items)
    assert all(item.reason == "duplicate_remote_id" for item in duplicated.items)


def test_duplicate_keys_do_not_use_array_position_or_last_write_wins() -> None:
    plan = SyncPlanner().plan(
        (_local("same", "first"), _local("same", "second")),
        (_remote("same", "remote"),),
        operation=SyncOperation.BIDIRECTIONAL,
    )

    assert len(plan.items) == 1
    assert plan.items[0].action is SyncAction.CONFLICT
    assert plan.items[0].reason == "duplicate_local_key"


def test_plan_hash_is_deterministic_order_independent_and_full_when_page_is_small() -> None:
    local = tuple(_local(f"key-{index:04}", str(index)) for index in range(1000))
    first = SyncPlanner().plan(
        local,
        (),
        operation=SyncOperation.UPLOAD,
    )
    second = SyncPlanner().plan(
        tuple(reversed(local)),
        (),
        operation=SyncOperation.UPLOAD,
    )

    page = first.to_dict(offset=10, limit=5)
    assert first.plan_hash == second.plan_hash
    assert first.plan_id == second.plan_id
    assert page["plan_hash"] == first.plan_hash
    assert page["total_items"] == 1000
    assert len(page["items"]) == 5
    assert page["has_more"] is True


def test_plan_projection_contains_hashes_not_source_or_translation_text() -> None:
    canary = "sensitive-translation-canary"
    plan = SyncPlanner().plan(
        (_local("key", canary),),
        (),
        operation=SyncOperation.UPLOAD,
    )

    projection = repr(plan.to_dict())
    assert canary not in projection
    assert "original-key" not in projection


def test_duplicate_snapshot_hash_is_order_independent() -> None:
    duplicates = (_local("key", "first"), _local("key", "second"))

    first = SyncPlanner().plan(duplicates, (), operation=SyncOperation.UPLOAD)
    second = SyncPlanner().plan(tuple(reversed(duplicates)), (), operation=SyncOperation.UPLOAD)

    assert first.plan_hash == second.plan_hash


class _RemoteReader:
    def __init__(self, entries: tuple[RemoteEntrySnapshot, ...]) -> None:
        self.entries = entries
        self.fetches = 0

    def fetch(self, project_id, namespace, *, limit, cancellation=None):
        del project_id, namespace, limit, cancellation
        self.fetches += 1
        return self.entries


def test_dry_run_only_reads_remote_and_never_calls_write_methods() -> None:
    service = MagicMock()
    service.list_entries.return_value = (ParaTranzEntry(1, "key", "original-key", "remote", "context", 1),)
    use_case = ParaTranzSyncPlanningUseCase(ParaTranzRemoteSnapshotAdapter(service))

    plan = use_case.create_plan(
        CreateSyncPlanRequest(
            project_id=7,
            namespace=NS,
            local_entries=(_local("key", "local", remote_id=1),),
            operation=SyncOperation.UPLOAD,
            conflict_policy=ConflictPolicy.PREFER_LOCAL,
        )
    )

    assert plan.items[0].action is SyncAction.UPDATE_REMOTE
    service.list_entries.assert_called_once_with(7, limit=100_001, cancellation=None)
    service.upsert_entry.assert_not_called()
    service.trigger_export.assert_not_called()


def test_remote_snapshot_limit_rejects_truncated_plan() -> None:
    service = MagicMock()
    service.list_entries.return_value = tuple(
        ParaTranzEntry(index, f"key-{index}", "original", "", "", 0) for index in range(1, 4)
    )
    adapter = ParaTranzRemoteSnapshotAdapter(service)

    with pytest.raises(ValueError, match="complete-plan limit"):
        adapter.fetch(7, NS, limit=2)

    service.list_entries.assert_called_once_with(7, limit=3, cancellation=None)


def test_remote_revision_fingerprint_is_stable_and_changes_with_remote_content() -> None:
    service = MagicMock()
    adapter = ParaTranzRemoteSnapshotAdapter(service)
    service.list_entries.return_value = (ParaTranzEntry(1, "key", "original", "first", "context", 1),)
    first = adapter.fetch(7, NS, limit=10)[0]
    same = adapter.fetch(7, NS, limit=10)[0]
    service.list_entries.return_value = (ParaTranzEntry(1, "key", "original", "changed", "context", 1),)
    changed = adapter.fetch(7, NS, limit=10)[0]

    assert first.remote_revision == same.remote_revision
    assert first.remote_revision != changed.remote_revision


def _destructive_use_case(*, clock=None):
    remote = (_remote("key", "remote", remote_id=1),)
    reader = _RemoteReader(remote)
    authority = ConfirmationAuthority(
        ttl_seconds=5,
        clock=(clock or (lambda: 10.0)),
        secret=b"s" * 32,
    )
    use_case = ParaTranzSyncPlanningUseCase(reader, confirmations=authority)
    local = (_local("key", "local", remote_id=1),)
    plan = use_case.create_plan(
        CreateSyncPlanRequest(
            7,
            NS,
            local,
            SyncOperation.UPLOAD,
            ConflictPolicy.PREFER_LOCAL,
        )
    )
    return use_case, reader, local, plan


def _authorization(plan, local, token, owner="owner") -> AuthorizeSyncPlanRequest:
    return AuthorizeSyncPlanRequest(
        plan,
        owner,
        7,
        NS,
        local,
        token,
    )


def test_confirmation_is_plan_hash_bound_owner_bound_and_one_use() -> None:
    use_case, _, local, plan = _destructive_use_case()
    token = use_case.issue_confirmation(plan, owner_id="owner")

    with pytest.raises(SyncPlanAuthorizationError) as wrong_owner:
        use_case.authorize(_authorization(plan, local, token, owner="other"))
    assert wrong_owner.value.code == "CONFIRMATION_OWNER_CHANGED"

    replay_token = use_case.issue_confirmation(plan, owner_id="owner")
    assert use_case.authorize(_authorization(plan, local, replay_token)).confirmation_code == "CONFIRMED"
    with pytest.raises(SyncPlanAuthorizationError) as replay:
        use_case.authorize(_authorization(plan, local, replay_token))
    assert replay.value.code == "CONFIRMATION_REPLAYED"


def test_destructive_overwrite_or_delete_without_confirmation_is_blocked() -> None:
    use_case, reader, local, overwrite_plan = _destructive_use_case()
    with pytest.raises(SyncPlanAuthorizationError) as missing_overwrite:
        use_case.authorize(_authorization(overwrite_plan, local, None))
    assert missing_overwrite.value.code == "CONFIRMATION_REQUIRED"

    deleted_local = (_local("key", "", deleted=True, remote_id=1),)
    delete_plan = use_case.create_plan(
        CreateSyncPlanRequest(
            7,
            NS,
            deleted_local,
            SyncOperation.UPLOAD,
            ConflictPolicy.ABORT,
        )
    )
    assert delete_plan.items[0].action is SyncAction.DELETE_REMOTE
    with pytest.raises(SyncPlanAuthorizationError) as missing_delete:
        use_case.authorize(_authorization(delete_plan, deleted_local, None))
    assert missing_delete.value.code == "CONFIRMATION_REQUIRED"
    assert reader.fetches == 4


def test_confirmation_expires_and_operation_or_plan_change_is_rejected() -> None:
    now = [10.0]
    use_case, _, local, plan = _destructive_use_case(clock=lambda: now[0])
    token = use_case.issue_confirmation(plan, owner_id="owner")
    now[0] = 16.0
    with pytest.raises(SyncPlanAuthorizationError) as expired:
        use_case.authorize(_authorization(plan, local, token))
    assert expired.value.code == "CONFIRMATION_EXPIRED"

    with pytest.raises(ValueError, match="hash"):
        replace(plan, operation=SyncOperation.DOWNLOAD)

    other_plan = SyncPlanner().plan(
        local,
        (_remote("key", "remote", remote_id=1),),
        operation=SyncOperation.BIDIRECTIONAL,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
        scope=plan.scope,
    )
    fresh_use_case, _, _, original_plan = _destructive_use_case()
    wrong_plan_token = fresh_use_case.issue_confirmation(original_plan, owner_id="owner")
    with pytest.raises(SyncPlanAuthorizationError) as changed_plan:
        fresh_use_case.authorize(_authorization(other_plan, local, wrong_plan_token))
    assert changed_plan.value.code == "CONFIRMATION_REQUEST_CHANGED"


@pytest.mark.parametrize("changed_side", ["local", "remote"])
def test_local_or_remote_revision_change_invalidates_plan_before_token_consumption(changed_side: str) -> None:
    use_case, reader, local, plan = _destructive_use_case()
    token = use_case.issue_confirmation(plan, owner_id="owner")
    current_local = local
    if changed_side == "local":
        current_local = (replace(local[0], revision=EntryRevision(2)),)
    else:
        reader.entries = (replace(reader.entries[0], remote_revision="remote-2"),)

    with pytest.raises(SyncPlanStaleError) as stale:
        use_case.authorize(_authorization(plan, current_local, token))
    assert stale.value.code == "STALE_PLAN"

    if changed_side == "local":
        # Restore freshness to prove stale validation did not consume confirmation.
        assert use_case.authorize(_authorization(plan, local, token)).confirmation_code == "CONFIRMED"


def test_non_destructive_create_plan_needs_no_confirmation_but_is_still_freshness_checked() -> None:
    reader = _RemoteReader(())
    use_case = ParaTranzSyncPlanningUseCase(reader)
    local = (_local("new", "translation"),)
    plan = use_case.create_plan(CreateSyncPlanRequest(7, NS, local, SyncOperation.UPLOAD))

    authorized = use_case.authorize(_authorization(plan, local, None))

    assert not plan.destructive
    assert authorized.confirmation_code == "NOT_REQUIRED"


def test_plan_and_confirmation_cannot_cross_project_scope() -> None:
    use_case, _, local, plan = _destructive_use_case()
    token = use_case.issue_confirmation(plan, owner_id="owner")

    with pytest.raises(SyncPlanAuthorizationError) as changed_scope:
        use_case.authorize(
            AuthorizeSyncPlanRequest(
                plan,
                "owner",
                8,
                NS,
                local,
                token,
            )
        )

    assert changed_scope.value.code == "PLAN_SCOPE_CHANGED"


def test_snapshot_dto_and_projection_do_not_expose_mutable_external_metadata() -> None:
    metadata = {"nested": {"value": 1}}
    source_ref = ExternalEntryRef("paratranz", "project:7", 1, (("mutable", metadata),))
    snapshot = LocalEntrySnapshot(
        _key("key"),
        EntryRevision(1),
        "original",
        "translation",
        external_ref=source_ref,
    )
    metadata["nested"]["value"] = 2

    assert snapshot.external_ref is not None
    assert snapshot.external_ref.metadata == ()
