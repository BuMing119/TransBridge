"""Immutable planning contracts for project terminology synchronization."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any

from .identity import sync_item_id
from .models import TerminologySyncMode


class TerminologySyncAction(StrEnum):
    CREATE_REMOTE = "create_remote"
    UPDATE_REMOTE = "update_remote"
    DELETE_REMOTE = "delete_remote"
    PROPOSE_LOCAL_ADD = "propose_local_add"
    PROPOSE_LOCAL_UPDATE = "propose_local_update"
    PROPOSE_LOCAL_SUPPRESSION = "propose_local_suppression"
    ADOPT_LINK = "adopt_link"
    SKIP = "skip"
    LOSSY_MAPPING = "lossy_mapping"
    CONFLICT = "conflict"
    BLOCKED = "blocked"

    @property
    def destructive(self) -> bool:
        return self in {
            TerminologySyncAction.UPDATE_REMOTE,
            TerminologySyncAction.DELETE_REMOTE,
            TerminologySyncAction.PROPOSE_LOCAL_UPDATE,
            TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION,
        }

    @property
    def executable_remote(self) -> bool:
        return self in {
            TerminologySyncAction.CREATE_REMOTE,
            TerminologySyncAction.UPDATE_REMOTE,
            TerminologySyncAction.DELETE_REMOTE,
        }


class TerminologySyncReason(StrEnum):
    LOCAL_ONLY = "local_only"
    INDEPENDENT_REMOTE = "independent_remote"
    UNCHANGED_ECHO = "unchanged_echo"
    SAFE_MATCH_PROPOSAL = "safe_match_proposal"
    LOCAL_CHANGED = "local_changed"
    REMOTE_CHANGED = "remote_changed"
    BOTH_CHANGED = "both_changed"
    LOCAL_DELETED = "local_deleted"
    REMOTE_DELETED = "remote_deleted"
    BOTH_DELETED = "both_deleted"
    UNKNOWN_OUTCOME = "unknown_outcome"
    REMOTE_ID_MISSING = "remote_id_missing"
    REMOTE_ID_REUSED = "remote_id_reused"
    DUPLICATE_LOCAL_IDENTITY = "duplicate_local_identity"
    DUPLICATE_REMOTE_IDENTITY = "duplicate_remote_identity"
    PLUGIN_SCOPE = "plugin_scope"
    SUPPRESSION_NOT_REPRESENTABLE = "suppression_not_representable"
    REPLACEMENT_NOT_REPRESENTABLE = "replacement_not_representable"
    VARIANT_MAPPING_CONFLICT = "variant_mapping_conflict"
    REMOTE_SNAPSHOT_UNSTABLE = "remote_snapshot_unstable"
    BASELINE_UNAVAILABLE = "baseline_unavailable"


@dataclass(frozen=True, slots=True)
class TerminologyContentSummary:
    """Canonical writable terminology content, excluding remote read-only fields."""

    original: str
    normalized_original: str
    translation: str
    scope: str
    suppressed: bool = False
    variants: tuple[str, ...] = ()
    case_sensitive: bool = False
    part_of_speech: str = ""
    note: str = ""
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.original.strip() or not self.normalized_original.strip():
            raise ValueError("terminology content requires an original term")
        if not self.suppressed and not self.translation.strip():
            raise ValueError("non-suppressed terminology content requires a translation")
        if not self.scope.strip():
            raise ValueError("terminology content requires a scope")
        object.__setattr__(self, "variants", tuple(sorted(set(self.variants))))
        expected = _digest(self.canonical_payload(include_digest=False))
        if self.digest and self.digest != expected:
            raise ValueError("terminology content digest does not match its canonical payload")
        object.__setattr__(self, "digest", expected)

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "original": self.original,
            "normalized_original": self.normalized_original,
            "translation": self.translation,
            "scope": self.scope,
            "suppressed": self.suppressed,
            "variants": list(self.variants),
            "case_sensitive": self.case_sensitive,
            "part_of_speech": self.part_of_speech,
            "note": self.note,
        }
        if include_digest:
            payload["digest"] = self.digest
        return payload


@dataclass(frozen=True, slots=True)
class TerminologySyncPlanItem:
    item_id: str
    action: TerminologySyncAction
    reason: TerminologySyncReason
    local_term_id: str | None = None
    remote_id: int | None = None
    base_digest: str | None = None
    local: TerminologyContentSummary | None = None
    remote: TerminologyContentSummary | None = None
    managed: bool = False
    requires_review: bool = False

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("plan item ID must not be empty")
        object.__setattr__(self, "action", TerminologySyncAction(self.action))
        object.__setattr__(self, "reason", TerminologySyncReason(self.reason))
        if self.local_term_id is not None and not self.local_term_id.strip():
            raise ValueError("local term ID must be absent or non-empty")
        if self.remote_id is not None and (isinstance(self.remote_id, bool) or self.remote_id < 1):
            raise ValueError("remote ID must be absent or a positive integer")
        if self.action.executable_remote and self.action is not TerminologySyncAction.CREATE_REMOTE:
            if self.remote_id is None:
                raise ValueError("remote update/delete plan items require a remote ID")
        if self.action is TerminologySyncAction.LOSSY_MAPPING and self.action.executable_remote:
            raise ValueError("lossy plan items can never carry an executable action")

    @property
    def destructive(self) -> bool:
        return self.action.destructive

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "action": self.action.value,
            "reason": self.reason.value,
            "local_term_id": self.local_term_id,
            "remote_id": self.remote_id,
            "base_digest": self.base_digest,
            "local": None if self.local is None else self.local.canonical_payload(),
            "remote": None if self.remote is None else self.remote.canonical_payload(),
            "managed": self.managed,
            "requires_review": self.requires_review,
        }


@dataclass(frozen=True, slots=True)
class TerminologySyncPlan:
    line_id: str
    target_identity: str
    binding_revision: int | None
    profile_revision: int
    mode: TerminologySyncMode
    local_project_id: str
    local_variant_id: str
    local_version_id: str
    local_content_digest: str
    remote_snapshot_digest: str
    baseline_revision: int | None
    items: tuple[TerminologySyncPlanItem, ...]
    diagnostics: tuple[str, ...] = ()
    plan_hash: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.line_id, "line ID"),
            (self.target_identity, "target identity"),
            (self.local_project_id, "local Project ID"),
            (self.local_variant_id, "local Variant ID"),
            (self.local_version_id, "local version ID"),
            (self.local_content_digest, "local content digest"),
            (self.remote_snapshot_digest, "remote snapshot digest"),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")
        for value, label in ((self.profile_revision, "profile revision"),):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.baseline_revision is not None and (
            isinstance(self.baseline_revision, bool) or self.baseline_revision < 0
        ):
            raise ValueError("baseline revision must be absent or non-negative")
        if self.binding_revision is not None and (isinstance(self.binding_revision, bool) or self.binding_revision < 0):
            raise ValueError("binding revision must be absent or non-negative")
        object.__setattr__(self, "mode", TerminologySyncMode(self.mode))
        items = tuple(sorted(self.items, key=lambda item: item.item_id))
        if len({item.item_id for item in items}) != len(items):
            raise ValueError("plan item IDs must be unique")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        expected = self.compute_hash()
        if self.plan_hash and self.plan_hash != expected:
            raise ValueError("terminology sync plan hash does not match its content")
        object.__setattr__(self, "plan_hash", expected)

    @property
    def plan_id(self) -> str:
        return f"terminology-sync-{self.plan_hash[:24]}"

    @property
    def counts(self) -> tuple[tuple[str, int], ...]:
        counts = Counter(item.action.value for item in self.items)
        return tuple(sorted(counts.items()))

    @property
    def blocked(self) -> bool:
        return any(item.action is TerminologySyncAction.BLOCKED for item in self.items)

    @property
    def has_conflicts(self) -> bool:
        return any(item.action is TerminologySyncAction.CONFLICT for item in self.items)

    @property
    def destructive(self) -> bool:
        return any(item.destructive for item in self.items)

    @property
    def requires_confirmation(self) -> bool:
        return self.destructive or any(item.requires_review for item in self.items)

    def compute_hash(self) -> str:
        return _digest({
            "schema": 1,
            "line_id": self.line_id,
            "target_identity": self.target_identity,
            "binding_revision": self.binding_revision,
            "profile_revision": self.profile_revision,
            "mode": TerminologySyncMode(self.mode).value,
            "local_project_id": self.local_project_id,
            "local_variant_id": self.local_variant_id,
            "local_version_id": self.local_version_id,
            "local_content_digest": self.local_content_digest,
            "remote_snapshot_digest": self.remote_snapshot_digest,
            "baseline_revision": self.baseline_revision,
            "items": [item.canonical_payload for item in sorted(self.items, key=lambda item: item.item_id)],
            "diagnostics": list(self.diagnostics),
        })


def stable_plan_item_id(
    *,
    line_id: str,
    local_term_id: str | None,
    remote_id: int | None,
    base_digest: str | None,
) -> str:
    if local_term_id is not None or remote_id is not None:
        return sync_item_id(line_id=line_id, local_term_id=local_term_id, remote_id=remote_id)
    return _digest({
        "line_id": line_id,
        "local_term_id": local_term_id,
        "remote_id": remote_id,
        "base_digest": base_digest,
    })


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "TerminologyContentSummary",
    "TerminologySyncAction",
    "TerminologySyncMode",
    "TerminologySyncPlan",
    "TerminologySyncPlanItem",
    "TerminologySyncReason",
    "stable_plan_item_id",
]
