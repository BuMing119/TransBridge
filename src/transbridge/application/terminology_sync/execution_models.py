"""Execution outcomes and retry identity for terminology synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json

from .plan_models import TerminologySyncAction


class TerminologySyncItemStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class TerminologySyncItemOutcome:
    item_id: str
    action: TerminologySyncAction
    status: TerminologySyncItemStatus
    code: str
    message: str
    attempt: int = 1
    remote_id: int | None = None
    remote_revision: str | None = None
    remote_observed_digest: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in ((self.item_id, "item ID"), (self.code, "outcome code"), (self.message, "message")):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")
        object.__setattr__(self, "action", TerminologySyncAction(self.action))
        object.__setattr__(self, "status", TerminologySyncItemStatus(self.status))
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if self.remote_id is not None and (isinstance(self.remote_id, bool) or self.remote_id < 1):
            raise ValueError("remote ID must be absent or positive")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "action": self.action.value,
            "status": self.status.value,
            "code": self.code,
            "attempt": self.attempt,
            "remote_id": self.remote_id,
            "remote_revision": self.remote_revision,
            "remote_observed_digest": self.remote_observed_digest,
            "request_id": self.request_id,
        }


@dataclass(frozen=True, slots=True)
class TerminologySyncRetryToken:
    line_id: str
    target_identity: str
    plan_hash: str
    owner_id: str
    confirmed_item_ids: tuple[str, ...]
    unknown_item_ids: tuple[str, ...]
    baseline_revision: int | None = None
    token_digest: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.line_id, "line ID"),
            (self.target_identity, "target identity"),
            (self.plan_hash, "plan hash"),
            (self.owner_id, "owner ID"),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")
        confirmed = tuple(sorted(set(self.confirmed_item_ids)))
        unknown = tuple(sorted(set(self.unknown_item_ids)))
        if set(confirmed) & set(unknown):
            raise ValueError("retry token item sets must be disjoint")
        if self.baseline_revision is not None and (
            isinstance(self.baseline_revision, bool) or self.baseline_revision < 0
        ):
            raise ValueError("retry baseline revision must be absent or non-negative")
        object.__setattr__(self, "confirmed_item_ids", confirmed)
        object.__setattr__(self, "unknown_item_ids", unknown)
        expected = self.compute_digest()
        if self.token_digest and self.token_digest != expected:
            raise ValueError("retry token digest does not match its content")
        object.__setattr__(self, "token_digest", expected)

    def compute_digest(self) -> str:
        payload = {
            "line_id": self.line_id,
            "target_identity": self.target_identity,
            "plan_hash": self.plan_hash,
            "owner_id": self.owner_id,
            "confirmed": list(self.confirmed_item_ids),
            "unknown": list(self.unknown_item_ids),
            "baseline_revision": self.baseline_revision,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TerminologyBackupExecutionResult:
    run_id: str
    plan_hash: str
    outcomes: tuple[TerminologySyncItemOutcome, ...]
    retry_token: TerminologySyncRetryToken | None = None
    reconcile_required: bool = False

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.plan_hash.strip():
            raise ValueError("execution result requires run and plan identities")
        outcomes = tuple(sorted(self.outcomes, key=lambda item: item.item_id))
        if len({item.item_id for item in outcomes}) != len(outcomes):
            raise ValueError("execution result item outcomes must be unique")
        object.__setattr__(self, "outcomes", outcomes)

    @property
    def partial(self) -> bool:
        statuses = {item.status for item in self.outcomes}
        return bool(
            statuses
            & {
                TerminologySyncItemStatus.FAILED,
                TerminologySyncItemStatus.UNKNOWN,
                TerminologySyncItemStatus.CANCELLED,
            }
        )


__all__ = [
    "TerminologyBackupExecutionResult",
    "TerminologySyncItemOutcome",
    "TerminologySyncItemStatus",
    "TerminologySyncRetryToken",
]
