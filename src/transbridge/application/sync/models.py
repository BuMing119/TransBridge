"""Immutable DTOs for ParaTranz synchronization planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef


class SyncOperation(StrEnum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    BIDIRECTIONAL = "bidirectional"


class ConflictPolicy(StrEnum):
    ABORT = "abort"
    PREFER_LOCAL = "prefer_local"
    PREFER_REMOTE = "prefer_remote"
    SKIP = "skip"


class SyncAction(StrEnum):
    CREATE_LOCAL = "create_local"
    CREATE_REMOTE = "create_remote"
    UPDATE_LOCAL = "update_local"
    UPDATE_REMOTE = "update_remote"
    DELETE_LOCAL = "delete_local"
    DELETE_REMOTE = "delete_remote"
    SKIP = "skip"
    CONFLICT = "conflict"

    @property
    def destructive(self) -> bool:
        return self in {
            SyncAction.UPDATE_LOCAL,
            SyncAction.UPDATE_REMOTE,
            SyncAction.DELETE_LOCAL,
            SyncAction.DELETE_REMOTE,
        }


def _clean_ref(reference: ExternalEntryRef | None) -> ExternalEntryRef | None:
    if reference is None:
        return None
    return ExternalEntryRef(reference.system, reference.scope, reference.opaque_id)


@dataclass(frozen=True, slots=True)
class LocalEntrySnapshot:
    entry_key: EntryKey
    revision: EntryRevision
    original: str
    translation: str
    context: str = ""
    stage: int = 0
    external_ref: ExternalEntryRef | None = None
    deleted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry_key, EntryKey):
            raise TypeError("local snapshot entry_key must be an EntryKey")
        if not isinstance(self.revision, EntryRevision):
            raise TypeError("local snapshot revision must be an EntryRevision")
        _validate_content(self.original, self.translation, self.context, self.stage, self.deleted)
        object.__setattr__(self, "external_ref", _clean_ref(self.external_ref))


@dataclass(frozen=True, slots=True)
class RemoteEntrySnapshot:
    entry_key: EntryKey
    remote_revision: str
    original: str
    translation: str
    context: str = ""
    stage: int = 0
    external_ref: ExternalEntryRef | None = None
    deleted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry_key, EntryKey):
            raise TypeError("remote snapshot entry_key must be an EntryKey")
        if not isinstance(self.remote_revision, str) or not self.remote_revision.strip():
            raise ValueError("remote snapshot revision must be a non-empty string")
        _validate_content(self.original, self.translation, self.context, self.stage, self.deleted)
        object.__setattr__(self, "external_ref", _clean_ref(self.external_ref))


def _validate_content(
    original: str,
    translation: str,
    context: str,
    stage: int,
    deleted: bool,
) -> None:
    if not all(isinstance(value, str) for value in (original, translation, context)):
        raise TypeError("snapshot text fields must be strings")
    if isinstance(stage, bool) or not isinstance(stage, int):
        raise TypeError("snapshot stage must be an integer")
    if not isinstance(deleted, bool):
        raise TypeError("snapshot deleted flag must be a boolean")


@dataclass(frozen=True, slots=True)
class EntrySummary:
    original_hash: str
    translation_hash: str
    context_hash: str
    stage: int
    deleted: bool
    revision: str

    @classmethod
    def from_local(cls, entry: LocalEntrySnapshot) -> EntrySummary:
        return cls(
            _text_hash(entry.original),
            _text_hash(entry.translation),
            _text_hash(entry.context),
            entry.stage,
            entry.deleted,
            str(entry.revision.value),
        )

    @classmethod
    def from_remote(cls, entry: RemoteEntrySnapshot) -> EntrySummary:
        return cls(
            _text_hash(entry.original),
            _text_hash(entry.translation),
            _text_hash(entry.context),
            entry.stage,
            entry.deleted,
            entry.remote_revision,
        )

    def content_identity(self) -> tuple[str, str, str, int, bool]:
        return (
            self.original_hash,
            self.translation_hash,
            self.context_hash,
            self.stage,
            self.deleted,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_hash": self.original_hash,
            "translation_hash": self.translation_hash,
            "context_hash": self.context_hash,
            "stage": self.stage,
            "deleted": self.deleted,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class SyncPlanItem:
    entry_key: EntryKey
    action: SyncAction
    before: EntrySummary | None
    after: EntrySummary | None
    external_ref: ExternalEntryRef | None
    reason: str
    conflict_policy: ConflictPolicy

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("sync plan item reason must not be empty")
        object.__setattr__(self, "external_ref", _clean_ref(self.external_ref))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_key": self.entry_key.to_dict(),
            "action": self.action.value,
            "before": None if self.before is None else self.before.to_dict(),
            "after": None if self.after is None else self.after.to_dict(),
            "external_ref": None if self.external_ref is None else self.external_ref.to_dict(),
            "reason": self.reason,
            "conflict_policy": self.conflict_policy.value,
        }


@dataclass(frozen=True, slots=True)
class SyncPlan:
    plan_id: str
    scope: str
    operation: SyncOperation
    conflict_policy: ConflictPolicy
    local_snapshot_hash: str
    remote_snapshot_hash: str
    items: tuple[SyncPlanItem, ...]
    counts: tuple[tuple[str, int], ...]
    conflicts: int
    destructive: bool
    plan_hash: str

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("sync plan id must not be empty")
        if not self.scope.strip():
            raise ValueError("sync plan scope must not be empty")
        if len(self.local_snapshot_hash) != 64 or len(self.remote_snapshot_hash) != 64:
            raise ValueError("sync snapshot hashes must be SHA-256 digests")
        if len(self.plan_hash) != 64:
            raise ValueError("sync plan hash must be a SHA-256 digest")
        if self.conflicts != sum(item.action is SyncAction.CONFLICT for item in self.items):
            raise ValueError("sync plan conflict count is inconsistent")
        if self.destructive != any(item.action.destructive for item in self.items):
            raise ValueError("sync plan destructive flag is inconsistent")
        expected_counts = tuple(sorted(_count_actions(self.items).items()))
        if self.counts != expected_counts:
            raise ValueError("sync plan action counts are inconsistent")
        if self.plan_hash != self.compute_hash():
            raise ValueError("sync plan hash does not match its immutable content")
        if self.plan_id != f"sync-{self.plan_hash[:20]}":
            raise ValueError("sync plan id must be derived from its plan hash")

    @property
    def requires_confirmation(self) -> bool:
        return self.destructive

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "operation": self.operation.value,
            "conflict_policy": self.conflict_policy.value,
            "local_snapshot_hash": self.local_snapshot_hash,
            "remote_snapshot_hash": self.remote_snapshot_hash,
            "items": [item.to_dict() for item in self.items],
            "counts": dict(self.counts),
            "conflicts": self.conflicts,
            "destructive": self.destructive,
        }

    def compute_hash(self) -> str:
        return _canonical_hash(self.canonical_payload())

    def to_dict(self, *, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError("limit must be a positive integer or None")
        stop = None if limit is None else offset + limit
        projected = self.items[offset:stop]
        return {
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "scope": self.scope,
            "operation": self.operation.value,
            "conflict_policy": self.conflict_policy.value,
            "local_snapshot_hash": self.local_snapshot_hash,
            "remote_snapshot_hash": self.remote_snapshot_hash,
            "counts": dict(self.counts),
            "conflicts": self.conflicts,
            "destructive": self.destructive,
            "requires_confirmation": self.requires_confirmation,
            "total_items": len(self.items),
            "offset": offset,
            "items": [item.to_dict() for item in projected],
            "has_more": offset + len(projected) < len(self.items),
        }


def _count_actions(items: tuple[SyncPlanItem, ...]) -> dict[str, int]:
    counts = {action.value: 0 for action in SyncAction}
    for item in items:
        counts[item.action.value] += 1
    return counts


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_hash(value: Any) -> str:
    """Return the shared canonical SHA-256 used by planner and adapters."""

    return _canonical_hash(value)
