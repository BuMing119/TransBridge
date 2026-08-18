"""Pure, deterministic synchronization planner."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from transbridge.application.io.identity import EntryKey

from .models import (
    ConflictPolicy,
    EntrySummary,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlan,
    SyncPlanItem,
    canonical_hash,
)


class SyncPlanner:
    """Compare immutable snapshots without network or repository side effects."""

    def plan(
        self,
        local_entries: Sequence[LocalEntrySnapshot],
        remote_entries: Sequence[RemoteEntrySnapshot],
        *,
        operation: SyncOperation,
        conflict_policy: ConflictPolicy = ConflictPolicy.ABORT,
        scope: str = "paratranz:unscoped",
    ) -> SyncPlan:
        operation = SyncOperation(operation)
        conflict_policy = ConflictPolicy(conflict_policy)
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("sync plan scope must not be empty")
        local = tuple(local_entries)
        remote = tuple(remote_entries)
        local_hash, remote_hash = self.snapshot_hashes(local, remote)
        local_groups = _group(local)
        remote_groups = _group(remote)
        duplicate_remote_refs = _duplicate_remote_refs(remote)
        items: list[SyncPlanItem] = []
        for key in sorted(set(local_groups) | set(remote_groups)):
            locals_for_key = local_groups.get(key, ())
            remotes_for_key = remote_groups.get(key, ())
            local_entry = locals_for_key[0] if locals_for_key else None
            remote_entry = remotes_for_key[0] if remotes_for_key else None
            if len(locals_for_key) > 1:
                items.append(_conflict(key, local_entry, remote_entry, conflict_policy, "duplicate_local_key"))
                continue
            if len(remotes_for_key) > 1:
                items.append(_conflict(key, local_entry, remote_entry, conflict_policy, "duplicate_remote_key"))
                continue
            if remote_entry is not None and remote_entry.external_ref is not None:
                if remote_entry.external_ref.index_key in duplicate_remote_refs:
                    items.append(_conflict(key, local_entry, remote_entry, conflict_policy, "duplicate_remote_id"))
                    continue
            items.append(_plan_item(key, local_entry, remote_entry, operation, conflict_policy))
        item_tuple = tuple(items)
        counts = Counter(item.action.value for item in item_tuple)
        full_counts = tuple(sorted((action.value, counts[action.value]) for action in SyncAction))
        conflicts = counts[SyncAction.CONFLICT.value]
        destructive = any(item.action.destructive for item in item_tuple)
        payload = {
            "scope": scope,
            "operation": operation.value,
            "conflict_policy": conflict_policy.value,
            "local_snapshot_hash": local_hash,
            "remote_snapshot_hash": remote_hash,
            "items": [item.to_dict() for item in item_tuple],
            "counts": dict(full_counts),
            "conflicts": conflicts,
            "destructive": destructive,
        }
        plan_hash = canonical_hash(payload)
        return SyncPlan(
            plan_id=f"sync-{plan_hash[:20]}",
            scope=scope,
            operation=operation,
            conflict_policy=conflict_policy,
            local_snapshot_hash=local_hash,
            remote_snapshot_hash=remote_hash,
            items=item_tuple,
            counts=full_counts,
            conflicts=conflicts,
            destructive=destructive,
            plan_hash=plan_hash,
        )

    def snapshot_hashes(
        self,
        local_entries: Sequence[LocalEntrySnapshot],
        remote_entries: Sequence[RemoteEntrySnapshot],
    ) -> tuple[str, str]:
        local_payload = sorted((_local_payload(item) for item in local_entries), key=_payload_sort_key)
        remote_payload = sorted((_remote_payload(item) for item in remote_entries), key=_payload_sort_key)
        return canonical_hash(local_payload), canonical_hash(remote_payload)


def _group(entries):
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry.entry_key].append(entry)
    return {key: tuple(sorted(value, key=_entry_sort_key)) for key, value in grouped.items()}


def _entry_sort_key(entry) -> str:
    if isinstance(entry, LocalEntrySnapshot):
        return canonical_hash(_local_payload(entry))
    return canonical_hash(_remote_payload(entry))


def _duplicate_remote_refs(entries: tuple[RemoteEntrySnapshot, ...]) -> set[tuple]:
    refs: dict[tuple, set[EntryKey]] = defaultdict(set)
    for entry in entries:
        if entry.external_ref is not None and entry.external_ref.opaque_id is not None:
            refs[entry.external_ref.index_key].add(entry.entry_key)
    return {ref for ref, keys in refs.items() if len(keys) > 1}


def _plan_item(
    key: EntryKey,
    local: LocalEntrySnapshot | None,
    remote: RemoteEntrySnapshot | None,
    operation: SyncOperation,
    policy: ConflictPolicy,
) -> SyncPlanItem:
    local_summary = None if local is None else EntrySummary.from_local(local)
    remote_summary = None if remote is None else EntrySummary.from_remote(remote)
    reference = remote.external_ref if remote is not None else (local.external_ref if local is not None else None)
    if local is None:
        if remote is None or remote.deleted:
            return _item(key, SyncAction.SKIP, remote_summary, remote_summary, reference, "no_live_entry", policy)
        if operation is SyncOperation.UPLOAD:
            return _item(key, SyncAction.SKIP, remote_summary, remote_summary, reference, "remote_only", policy)
        return _item(key, SyncAction.CREATE_LOCAL, None, remote_summary, reference, "remote_only", policy)
    if remote is None:
        if local.deleted:
            return _item(key, SyncAction.SKIP, local_summary, local_summary, reference, "local_tombstone_only", policy)
        if operation is SyncOperation.DOWNLOAD:
            return _item(key, SyncAction.SKIP, local_summary, local_summary, reference, "local_only", policy)
        return _item(key, SyncAction.CREATE_REMOTE, None, local_summary, reference, "local_only", policy)
    if local.deleted and remote.deleted:
        return _item(key, SyncAction.SKIP, remote_summary, local_summary, reference, "both_deleted", policy)
    if local.deleted:
        if operation is SyncOperation.DOWNLOAD:
            return _item(
                key, SyncAction.UPDATE_LOCAL, local_summary, remote_summary, reference, "restore_from_remote", policy
            )
        if remote.external_ref is None or remote.external_ref.opaque_id is None:
            return _conflict(key, local, remote, policy, "remote_id_missing")
        return _item(key, SyncAction.DELETE_REMOTE, remote_summary, local_summary, reference, "local_tombstone", policy)
    if remote.deleted:
        if operation is SyncOperation.UPLOAD:
            return _item(
                key, SyncAction.UPDATE_REMOTE, remote_summary, local_summary, reference, "restore_from_local", policy
            )
        return _item(key, SyncAction.DELETE_LOCAL, local_summary, remote_summary, reference, "remote_tombstone", policy)
    if local_summary.content_identity() == remote_summary.content_identity():
        return _item(key, SyncAction.SKIP, remote_summary, local_summary, reference, "unchanged", policy)
    if policy is ConflictPolicy.ABORT:
        return _conflict(key, local, remote, policy, "content_changed")
    if policy is ConflictPolicy.SKIP:
        return _item(key, SyncAction.SKIP, remote_summary, local_summary, reference, "conflict_skipped", policy)
    if operation is SyncOperation.UPLOAD:
        if policy is ConflictPolicy.PREFER_REMOTE:
            return _item(key, SyncAction.SKIP, remote_summary, remote_summary, reference, "remote_preferred", policy)
        if remote.external_ref is None or remote.external_ref.opaque_id is None:
            return _conflict(key, local, remote, policy, "remote_id_missing")
        return _item(key, SyncAction.UPDATE_REMOTE, remote_summary, local_summary, reference, "local_preferred", policy)
    if operation is SyncOperation.DOWNLOAD:
        if policy is ConflictPolicy.PREFER_LOCAL:
            return _item(key, SyncAction.SKIP, local_summary, local_summary, reference, "local_preferred", policy)
        return _item(key, SyncAction.UPDATE_LOCAL, local_summary, remote_summary, reference, "remote_preferred", policy)
    if policy is ConflictPolicy.PREFER_LOCAL:
        if remote.external_ref is None or remote.external_ref.opaque_id is None:
            return _conflict(key, local, remote, policy, "remote_id_missing")
        return _item(key, SyncAction.UPDATE_REMOTE, remote_summary, local_summary, reference, "local_preferred", policy)
    return _item(key, SyncAction.UPDATE_LOCAL, local_summary, remote_summary, reference, "remote_preferred", policy)


def _conflict(key, local, remote, policy, reason):
    return _item(
        key,
        SyncAction.CONFLICT,
        None if remote is None else EntrySummary.from_remote(remote),
        None if local is None else EntrySummary.from_local(local),
        None if remote is None else remote.external_ref,
        reason,
        policy,
    )


def _item(key, action, before, after, reference, reason, policy):
    return SyncPlanItem(key, action, before, after, reference, reason, policy)


def _local_payload(entry: LocalEntrySnapshot) -> dict:
    return {
        "entry_key": entry.entry_key.to_dict(),
        "revision": entry.revision.value,
        "original": entry.original,
        "translation": entry.translation,
        "context": entry.context,
        "stage": entry.stage,
        "external_ref": None if entry.external_ref is None else entry.external_ref.to_dict(),
        "deleted": entry.deleted,
    }


def _remote_payload(entry: RemoteEntrySnapshot) -> dict:
    return {
        "entry_key": entry.entry_key.to_dict(),
        "remote_revision": entry.remote_revision,
        "original": entry.original,
        "translation": entry.translation,
        "context": entry.context,
        "stage": entry.stage,
        "external_ref": None if entry.external_ref is None else entry.external_ref.to_dict(),
        "deleted": entry.deleted,
    }


def _payload_sort_key(payload: dict) -> tuple[str, str, str, str]:
    key = payload["entry_key"]
    reference = payload.get("external_ref") or {}
    return (
        key["namespace"],
        key["local_key"],
        repr(reference.get("opaque_id")),
        canonical_hash(payload),
    )
