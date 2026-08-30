"""Immutable application contracts for terminology synchronization state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .identity import sync_line_id, target_id


class TerminologySyncMode(StrEnum):
    BACKUP = "backup"
    BIDIRECTIONAL = "bidirectional"


class TerminologyLossyPolicy(StrEnum):
    SKIP = "skip"
    REQUIRE_CONFIRMATION = "require_confirmation"


class TerminologyDeletePolicy(StrEnum):
    PRESERVE = "preserve"
    MANAGED_ONLY = "managed_only"


class TerminologySyncOwnership(StrEnum):
    MANAGED = "managed"
    REMOTE_INDEPENDENT = "remote_independent"


class TerminologySyncTombstone(StrEnum):
    LIVE = "live"
    LOCAL_DELETED = "local_deleted"
    REMOTE_DELETED = "remote_deleted"
    BOTH_DELETED = "both_deleted"


class TerminologySyncOutcome(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILED = "reconciled"


class TerminologySyncRunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TerminologySyncTarget:
    endpoint: str
    account_user_id: int | None
    remote_project_id: int

    def __post_init__(self) -> None:
        from .identity import target_payload

        payload = target_payload(
            endpoint=self.endpoint,
            account_user_id=self.account_user_id,
            remote_project_id=self.remote_project_id,
        )
        object.__setattr__(self, "endpoint", payload["endpoint"])

    @property
    def target_id(self) -> str:
        return target_id(
            endpoint=self.endpoint,
            account_user_id=self.account_user_id,
            remote_project_id=self.remote_project_id,
        )

    @property
    def verified(self) -> bool:
        return self.account_user_id is not None


@dataclass(frozen=True, slots=True)
class TerminologySyncTargetBinding:
    """Fresh Project binding evidence checked immediately before remote work."""

    project_id: str
    target: TerminologySyncTarget
    revision: int | None

    def __post_init__(self) -> None:
        _required(self.project_id, "binding Project ID")
        if self.revision is not None:
            _revision(self.revision, "binding revision")


@dataclass(frozen=True, slots=True)
class TerminologySyncLine:
    line_id: str
    project_id: str
    variant_id: str
    target: TerminologySyncTarget
    profile_revision: int
    created_at: str
    retired_at: str | None = None

    def __post_init__(self) -> None:
        _required(self.project_id, "project ID")
        _required(self.variant_id, "variant ID")
        _revision(self.profile_revision, "profile revision")
        _timestamp(self.created_at, "line created_at")
        if self.retired_at is not None:
            _timestamp(self.retired_at, "line retired_at")
        expected = sync_line_id(
            project_id=self.project_id,
            variant_id=self.variant_id,
            target_identity=self.target.target_id,
            profile_revision=self.profile_revision,
        )
        if self.line_id != expected:
            raise ValueError("line ID does not match its canonical identity")

    @property
    def active(self) -> bool:
        return self.retired_at is None


@dataclass(frozen=True, slots=True)
class TerminologySyncProfile:
    line_id: str
    revision: int
    mode: TerminologySyncMode = TerminologySyncMode.BACKUP
    lossy_policy: TerminologyLossyPolicy = TerminologyLossyPolicy.SKIP
    delete_policy: TerminologyDeletePolicy = TerminologyDeletePolicy.MANAGED_ONLY
    mapping_revision: int = 0
    automatic_sync: bool = False

    def __post_init__(self) -> None:
        _required(self.line_id, "line ID")
        _revision(self.revision, "profile revision")
        _revision(self.mapping_revision, "mapping revision")
        object.__setattr__(self, "mode", TerminologySyncMode(self.mode))
        object.__setattr__(self, "lossy_policy", TerminologyLossyPolicy(self.lossy_policy))
        object.__setattr__(self, "delete_policy", TerminologyDeletePolicy(self.delete_policy))
        if not isinstance(self.automatic_sync, bool):
            raise TypeError("automatic_sync must be a boolean")


@dataclass(frozen=True, slots=True)
class TerminologySyncBaseline:
    line_id: str
    revision: int
    local_version_id: str
    local_content_digest: str
    remote_snapshot_digest: str
    common_snapshot_digest: str
    completed_run_id: str

    def __post_init__(self) -> None:
        _required(self.line_id, "line ID")
        _revision(self.revision, "baseline revision")
        _required(self.local_version_id, "local version ID")
        _required(self.local_content_digest, "local content digest")
        _required(self.remote_snapshot_digest, "remote snapshot digest")
        _required(self.common_snapshot_digest, "common snapshot digest")
        _required(self.completed_run_id, "completed run ID")


@dataclass(frozen=True, slots=True)
class TerminologySyncItemLink:
    line_id: str
    item_id: str
    revision: int
    local_term_id: str | None
    local_version_id: str | None
    local_content_digest: str | None
    remote_id: int | None
    remote_revision: str | None
    remote_observed_digest: str | None
    common_content_digest: str | None
    scope: str
    ownership: TerminologySyncOwnership
    tombstone: TerminologySyncTombstone = TerminologySyncTombstone.LIVE
    last_outcome: TerminologySyncOutcome | None = None

    def __post_init__(self) -> None:
        _required(self.line_id, "line ID")
        _required(self.item_id, "item ID")
        _revision(self.revision, "item link revision")
        if self.local_term_id is None and self.remote_id is None:
            raise ValueError("item link requires a local term ID or remote ID")
        _optional_triplet(
            (self.local_term_id, self.local_version_id, self.local_content_digest),
            "local term/version/digest",
        )
        if self.remote_id is not None:
            _positive_integer(self.remote_id, "remote ID")
        _optional_required(self.remote_revision, "remote revision")
        _optional_required(self.remote_observed_digest, "remote observed digest")
        if self.remote_id is not None and self.remote_revision is None and self.remote_observed_digest is None:
            raise ValueError("remote item link requires a revision or observed digest")
        _optional_required(self.common_content_digest, "common content digest")
        _required(self.scope, "scope")
        object.__setattr__(self, "ownership", TerminologySyncOwnership(self.ownership))
        object.__setattr__(self, "tombstone", TerminologySyncTombstone(self.tombstone))
        if self.last_outcome is not None:
            object.__setattr__(self, "last_outcome", TerminologySyncOutcome(self.last_outcome))


@dataclass(frozen=True, slots=True)
class TerminologySyncRunRecord:
    run_id: str
    line_id: str
    plan_id: str
    owner_id: str
    target_id: str
    baseline_revision: int | None
    outcome: TerminologySyncRunOutcome
    started_at: str
    completed_at: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.run_id, "run ID"),
            (self.line_id, "line ID"),
            (self.plan_id, "plan ID"),
            (self.owner_id, "owner ID"),
            (self.target_id, "target ID"),
        ):
            _required(value, label)
        if self.baseline_revision is not None:
            _revision(self.baseline_revision, "run baseline revision")
        object.__setattr__(self, "outcome", TerminologySyncRunOutcome(self.outcome))
        _timestamp(self.started_at, "run started_at")
        _timestamp(self.completed_at, "run completed_at")


@dataclass(frozen=True, slots=True)
class TerminologySyncItemOutcomeRecord:
    outcome_id: str
    run_id: str
    line_id: str
    item_id: str
    status: TerminologySyncOutcome
    code: str
    message: str
    recorded_at: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.outcome_id, "outcome ID"),
            (self.run_id, "run ID"),
            (self.line_id, "line ID"),
            (self.item_id, "item ID"),
            (self.code, "outcome code"),
            (self.message, "outcome message"),
        ):
            _required(value, label)
        object.__setattr__(self, "status", TerminologySyncOutcome(self.status))
        _timestamp(self.recorded_at, "outcome recorded_at")


@dataclass(frozen=True, slots=True)
class TerminologySyncItemLinkUpdate:
    link: TerminologySyncItemLink
    expected_revision: int | None

    def __post_init__(self) -> None:
        if self.expected_revision is not None:
            _revision(self.expected_revision, "expected item link revision")


@dataclass(frozen=True, slots=True)
class TerminologySyncCommit:
    run: TerminologySyncRunRecord
    outcomes: tuple[TerminologySyncItemOutcomeRecord, ...]
    baseline: TerminologySyncBaseline
    item_links: tuple[TerminologySyncItemLinkUpdate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        object.__setattr__(self, "item_links", tuple(self.item_links))
        if self.run.line_id != self.baseline.line_id:
            raise ValueError("run and baseline must belong to the same line")
        if self.baseline.completed_run_id != self.run.run_id:
            raise ValueError("baseline must identify the committing run")
        if any(outcome.run_id != self.run.run_id or outcome.line_id != self.run.line_id for outcome in self.outcomes):
            raise ValueError("all outcomes must belong to the committing run and line")
        if any(update.link.line_id != self.run.line_id for update in self.item_links):
            raise ValueError("all item links must belong to the committing line")


@dataclass(frozen=True, slots=True)
class TerminologySyncLineState:
    line: TerminologySyncLine | None
    profile: TerminologySyncProfile | None
    baseline: TerminologySyncBaseline | None
    writable: bool
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.writable, bool):
            raise TypeError("line state writable must be a boolean")
        if self.diagnostic is not None:
            _required(self.diagnostic, "line state diagnostic")
        if self.line is None and (self.profile is not None or self.baseline is not None):
            raise ValueError("profile and baseline require a line")
        if self.line is not None:
            if self.profile is not None and self.profile.line_id != self.line.line_id:
                raise ValueError("profile belongs to another sync line")
            if self.baseline is not None and self.baseline.line_id != self.line.line_id:
                raise ValueError("baseline belongs to another sync line")


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _optional_required(value: str | None, label: str) -> None:
    if value is not None:
        _required(value, label)


def _revision(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _timestamp(value: str, label: str) -> None:
    raw = _required(value, label)
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO 8601 timestamp") from exc


def _optional_triplet(values: tuple[str | None, str | None, str | None], label: str) -> None:
    present = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise ValueError(f"{label} must be either complete or absent")
    for value in values:
        _optional_required(value, label)


__all__ = [
    "TerminologyDeletePolicy",
    "TerminologyLossyPolicy",
    "TerminologySyncBaseline",
    "TerminologySyncCommit",
    "TerminologySyncItemLink",
    "TerminologySyncItemLinkUpdate",
    "TerminologySyncItemOutcomeRecord",
    "TerminologySyncLine",
    "TerminologySyncLineState",
    "TerminologySyncMode",
    "TerminologySyncOutcome",
    "TerminologySyncOwnership",
    "TerminologySyncProfile",
    "TerminologySyncRunOutcome",
    "TerminologySyncRunRecord",
    "TerminologySyncTarget",
    "TerminologySyncTargetBinding",
    "TerminologySyncTombstone",
]
