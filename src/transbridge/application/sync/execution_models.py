"""Immutable journal and retry contracts for transactional synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from transbridge.application.io.identity import EntryKey, ExternalEntryRef

from .models import SyncAction, canonical_hash


class SyncItemStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def confirmed(self) -> bool:
        return self in {SyncItemStatus.SUCCEEDED, SyncItemStatus.SKIPPED}


@dataclass(frozen=True, slots=True)
class SyncItemOutcome:
    """Secret-free outcome for one immutable plan item."""

    item_id: str
    entry_key: EntryKey
    action: SyncAction
    status: SyncItemStatus
    code: str
    message: str
    retryable: bool = False
    external_ref: ExternalEntryRef | None = None
    remote_revision: str | None = None

    def __post_init__(self) -> None:
        if not _is_digest(self.item_id):
            raise ValueError("sync item id must be a SHA-256 digest")
        if not self.code.strip() or not self.message.strip():
            raise ValueError("sync item outcome code and message must not be empty")
        if self.remote_revision is not None and not self.remote_revision.strip():
            raise ValueError("remote revision must be non-empty or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "entry_key": self.entry_key.to_dict(),
            "action": self.action.value,
            "status": self.status.value,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "external_ref": None if self.external_ref is None else self.external_ref.to_dict(),
            "remote_revision": self.remote_revision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncItemOutcome:
        retryable = data.get("retryable", False)
        if not isinstance(retryable, bool):
            raise TypeError("sync item retryable must be a boolean")
        reference = data.get("external_ref")
        return cls(
            item_id=str(data["item_id"]),
            entry_key=EntryKey.from_dict(data["entry_key"]),
            action=SyncAction(data["action"]),
            status=SyncItemStatus(data["status"]),
            code=str(data["code"]),
            message=str(data["message"]),
            retryable=retryable,
            external_ref=None if reference is None else ExternalEntryRef.from_dict(reference),
            remote_revision=None if data.get("remote_revision") is None else str(data["remote_revision"]),
        )


@dataclass(frozen=True, slots=True)
class RetryToken:
    """Tamper-evident checkpoint bound to a plan, owner and item journal."""

    schema_version: int
    plan_hash: str
    owner_id: str
    outcomes: tuple[SyncItemOutcome, ...]
    token_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported retry token schema version")
        if not _is_digest(self.plan_hash) or not _is_digest(self.token_hash):
            raise ValueError("retry token hashes must be SHA-256 digests")
        if not self.owner_id.strip():
            raise ValueError("retry token owner_id must not be empty")
        ids = [outcome.item_id for outcome in self.outcomes]
        if len(ids) != len(set(ids)):
            raise ValueError("retry token contains duplicate item outcomes")
        if self.token_hash != self.compute_hash():
            raise ValueError("retry token content does not match its hash")

    @classmethod
    def issue(
        cls,
        *,
        plan_hash: str,
        owner_id: str,
        outcomes: tuple[SyncItemOutcome, ...],
    ) -> RetryToken:
        ordered = tuple(sorted(outcomes, key=lambda outcome: outcome.item_id))
        payload = _retry_payload(1, plan_hash, owner_id, ordered)
        return cls(1, plan_hash, owner_id, ordered, canonical_hash(payload))

    @property
    def confirmed_item_ids(self) -> frozenset[str]:
        return frozenset(outcome.item_id for outcome in self.outcomes if outcome.status.confirmed)

    def compute_hash(self) -> str:
        return canonical_hash(_retry_payload(self.schema_version, self.plan_hash, self.owner_id, self.outcomes))

    def validate_binding(self, *, plan_hash: str, owner_id: str, valid_item_ids: frozenset[str]) -> None:
        if self.plan_hash != plan_hash or self.owner_id != owner_id:
            raise ValueError("retry token is not bound to this plan and owner")
        if any(outcome.item_id not in valid_item_ids for outcome in self.outcomes):
            raise ValueError("retry token contains an item outside the bound plan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "owner_id": self.owner_id,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "token_hash": self.token_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryToken:
        version = data["schema_version"]
        outcomes = data.get("outcomes", ())
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("retry token schema_version must be an integer")
        if not isinstance(outcomes, list):
            raise TypeError("retry token outcomes must be an array")
        if not isinstance(data.get("plan_hash"), str) or not isinstance(data.get("owner_id"), str):
            raise TypeError("retry token binding fields must be strings")
        if not isinstance(data.get("token_hash"), str):
            raise TypeError("retry token hash must be a string")
        return cls(
            schema_version=version,
            plan_hash=data["plan_hash"],
            owner_id=data["owner_id"],
            outcomes=tuple(SyncItemOutcome.from_dict(item) for item in outcomes),
            token_hash=data["token_hash"],
        )


def sync_item_id(plan_hash: str, action: SyncAction, entry_key: EntryKey) -> str:
    return canonical_hash({
        "plan_hash": plan_hash,
        "action": action.value,
        "entry_key": entry_key.to_dict(),
    })


def _retry_payload(
    schema_version: int,
    plan_hash: str,
    owner_id: str,
    outcomes: tuple[SyncItemOutcome, ...],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "plan_hash": plan_hash,
        "owner_id": owner_id,
        "outcomes": [outcome.to_dict() for outcome in outcomes],
    }


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
