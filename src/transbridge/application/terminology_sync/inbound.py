"""Immutable inbound facts produced by bidirectional terminology plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from transbridge.application.ports.paratranz_terms import ParaTranzTermSnapshot
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import DraftRef

from .models import TerminologySyncMode, TerminologySyncRunOutcome
from .plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncPlan,
    TerminologySyncPlanItem,
)


class InboundChangeKind(StrEnum):
    REMOTE_ADD = "remote_add"
    REMOTE_UPDATE = "remote_update"
    REMOTE_DELETE = "remote_delete"
    REMOTE_CONFLICT = "remote_conflict"


class InboundProposedEffect(StrEnum):
    ADD = "add"
    UPDATE = "update"
    SUPPRESS = "suppress"
    CONFLICT = "conflict"


class InboundReviewStatus(StrEnum):
    PENDING = "pending"
    PARTIALLY_REVIEWED = "partially_reviewed"
    REVIEWED = "reviewed"
    STALE = "stale"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EDITED = "edited"
    CONFLICT = "conflict"


class InboundReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"


_ITEM_STATUSES = frozenset({
    InboundReviewStatus.PENDING,
    InboundReviewStatus.ACCEPTED,
    InboundReviewStatus.REJECTED,
    InboundReviewStatus.EDITED,
    InboundReviewStatus.CONFLICT,
})
_SET_STATUSES = frozenset({
    InboundReviewStatus.PENDING,
    InboundReviewStatus.PARTIALLY_REVIEWED,
    InboundReviewStatus.REVIEWED,
    InboundReviewStatus.STALE,
})


@dataclass(frozen=True, slots=True)
class InboundTerminologyChange:
    item_id: str
    kind: InboundChangeKind
    remote_id: int
    remote_revision: str | None
    remote_observed_digest: str | None
    base_digest: str | None
    local_term_id: str | None
    local: TerminologyContentSummary | None
    remote: TerminologyContentSummary | None
    proposed_effect: InboundProposedEffect
    reason: str
    content_digest: str = ""

    def __post_init__(self) -> None:
        _required(self.item_id, "inbound item ID")
        object.__setattr__(self, "kind", InboundChangeKind(self.kind))
        _positive_integer(self.remote_id, "remote ID")
        for value, label in (
            (self.remote_revision, "remote revision"),
            (self.remote_observed_digest, "remote observed digest"),
            (self.base_digest, "base digest"),
            (self.local_term_id, "local term ID"),
        ):
            _optional_required(value, label)
        object.__setattr__(self, "proposed_effect", InboundProposedEffect(self.proposed_effect))
        _required(self.reason, "inbound reason")
        if self.kind is InboundChangeKind.REMOTE_ADD and self.remote is None:
            raise ValueError("remote add requires remote terminology content")
        if self.kind is InboundChangeKind.REMOTE_UPDATE and (self.local is None or self.remote is None):
            raise ValueError("remote update requires local and remote terminology content")
        if self.kind is InboundChangeKind.REMOTE_DELETE and self.local is None:
            raise ValueError("remote delete requires local terminology content")
        expected = canonical_digest(
            self.canonical_payload(include_digest=False),
            namespace="terminology-sync.inbound-item.v1",
        )
        if self.content_digest and self.content_digest != expected:
            raise ValueError("inbound item content digest does not match its facts")
        object.__setattr__(self, "content_digest", expected)

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "remote_id": self.remote_id,
            "remote_revision": self.remote_revision,
            "remote_observed_digest": self.remote_observed_digest,
            "base_digest": self.base_digest,
            "local_term_id": self.local_term_id,
            "local": None if self.local is None else self.local.canonical_payload(),
            "remote": None if self.remote is None else self.remote.canonical_payload(),
            "proposed_effect": self.proposed_effect.value,
            "reason": self.reason,
        }
        if include_digest:
            payload["content_digest"] = self.content_digest
        return payload


@dataclass(frozen=True, slots=True)
class InboundTerminologyChangeSet:
    change_set_id: str
    revision: int
    line_id: str
    project_id: str
    variant_id: str
    target_identity: str
    remote_project_id: int
    plan_id: str
    plan_hash: str
    baseline_revision: int | None
    remote_snapshot_digest: str
    source_run_id: str
    source_run_outcome: TerminologySyncRunOutcome
    items: tuple[InboundTerminologyChange, ...]
    content_digest: str
    created_at: datetime

    def __post_init__(self) -> None:
        for value, label in (
            (self.change_set_id, "change set ID"),
            (self.line_id, "sync line ID"),
            (self.project_id, "local Project ID"),
            (self.variant_id, "local Variant ID"),
            (self.target_identity, "target identity"),
            (self.plan_id, "source plan ID"),
            (self.plan_hash, "source plan hash"),
            (self.remote_snapshot_digest, "remote snapshot digest"),
            (self.source_run_id, "source run ID"),
            (self.content_digest, "change set content digest"),
        ):
            _required(value, label)
        _revision(self.revision, "change set revision")
        if self.baseline_revision is not None:
            _revision(self.baseline_revision, "baseline revision")
        _positive_integer(self.remote_project_id, "remote project ID")
        object.__setattr__(self, "source_run_outcome", TerminologySyncRunOutcome(self.source_run_outcome))
        items = tuple(sorted(self.items, key=lambda item: item.item_id))
        if not items:
            raise ValueError("inbound change set requires at least one item")
        if len({item.item_id for item in items}) != len(items):
            raise ValueError("inbound change set item IDs must be unique")
        object.__setattr__(self, "items", items)
        created_at = _aware(self.created_at, "change set created_at")
        object.__setattr__(self, "created_at", created_at.astimezone(UTC))
        identity = _change_set_identity(
            self.line_id,
            self.baseline_revision,
            self.remote_snapshot_digest,
            items,
        )
        if self.change_set_id != identity:
            raise ValueError("change set ID does not match its idempotent identity")
        expected_digest = canonical_digest(
            self.canonical_payload(include_digest=False),
            namespace="terminology-sync.inbound-set-content.v1",
        )
        if self.content_digest != expected_digest:
            raise ValueError("change set content digest does not match its immutable facts")

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "change_set_id": self.change_set_id,
            "revision": self.revision,
            "line_id": self.line_id,
            "project_id": self.project_id,
            "variant_id": self.variant_id,
            "target_identity": self.target_identity,
            "remote_project_id": self.remote_project_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "baseline_revision": self.baseline_revision,
            "remote_snapshot_digest": self.remote_snapshot_digest,
            "source_run_id": self.source_run_id,
            "source_run_outcome": self.source_run_outcome.value,
            "items": [item.canonical_payload() for item in self.items],
            "created_at": self.created_at.astimezone(UTC).isoformat(),
        }
        if include_digest:
            payload["content_digest"] = self.content_digest
        return payload


@dataclass(frozen=True, slots=True)
class InboundItemDisposition:
    item_id: str
    status: InboundReviewStatus
    actor: str
    occurred_at: datetime
    before_digest: str
    after_digest: str
    proposal_digest: str
    remote_id: int
    remote_revision: str | None
    remote_observed_digest: str | None
    action_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.item_id, "disposition item ID"),
            (self.actor, "disposition actor"),
            (self.before_digest, "disposition before digest"),
            (self.after_digest, "disposition after digest"),
            (self.proposal_digest, "disposition proposal digest"),
        ):
            _required(value, label)
        status = InboundReviewStatus(self.status)
        if status not in _ITEM_STATUSES - {InboundReviewStatus.PENDING}:
            raise ValueError("disposition requires a reviewed item status")
        object.__setattr__(self, "status", status)
        _positive_integer(self.remote_id, "disposition remote ID")
        for value, label in (
            (self.remote_revision, "disposition remote revision"),
            (self.remote_observed_digest, "disposition remote observed digest"),
            (self.action_id, "disposition action ID"),
            (self.reason, "disposition reason"),
        ):
            _optional_required(value, label)
        occurred_at = _aware(self.occurred_at, "disposition occurred_at")
        object.__setattr__(self, "occurred_at", occurred_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class InboundAppliedProposal:
    proposal_digest: str
    draft_ref: DraftRef | None
    action_ids: tuple[str, ...]
    committed_at: datetime

    def __post_init__(self) -> None:
        _required(self.proposal_digest, "applied proposal digest")
        action_ids = tuple(sorted(_required(item, "applied action ID") for item in self.action_ids))
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("applied action IDs must be unique")
        object.__setattr__(self, "action_ids", action_ids)
        committed_at = _aware(self.committed_at, "proposal committed_at")
        object.__setattr__(self, "committed_at", committed_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class InboundReviewState:
    change_set_id: str
    revision: int
    status: InboundReviewStatus
    dispositions: tuple[InboundItemDisposition, ...] = ()
    applied_proposals: tuple[InboundAppliedProposal, ...] = ()

    def __post_init__(self) -> None:
        _required(self.change_set_id, "review change set ID")
        _revision(self.revision, "review revision")
        status = InboundReviewStatus(self.status)
        if status not in _SET_STATUSES:
            raise ValueError("review state requires a change-set status")
        object.__setattr__(self, "status", status)
        dispositions = tuple(sorted(self.dispositions, key=lambda item: item.item_id))
        if len({item.item_id for item in dispositions}) != len(dispositions):
            raise ValueError("review dispositions must have unique item IDs")
        proposals = tuple(sorted(self.applied_proposals, key=lambda item: item.proposal_digest))
        if len({item.proposal_digest for item in proposals}) != len(proposals):
            raise ValueError("applied proposal digests must be unique")
        object.__setattr__(self, "dispositions", dispositions)
        object.__setattr__(self, "applied_proposals", proposals)


class InboundChangeSetStorePort(Protocol):
    def save_change_set(self, change_set: InboundTerminologyChangeSet) -> InboundTerminologyChangeSet: ...

    def get_change_set(self, change_set_id: str) -> InboundTerminologyChangeSet: ...

    def get_review_state(self, change_set_id: str) -> InboundReviewState: ...

    def commit_review(
        self,
        change_set_id: str,
        *,
        expected_revision: int,
        dispositions: tuple[InboundItemDisposition, ...],
        applied_proposal: InboundAppliedProposal,
    ) -> InboundReviewState: ...


def build_inbound_change_set(
    plan: TerminologySyncPlan,
    remote_snapshot: ParaTranzTermSnapshot,
    *,
    source_run_id: str,
    source_run_outcome: TerminologySyncRunOutcome,
    created_at: datetime,
) -> InboundTerminologyChangeSet:
    """Freeze all local-facing facts from one stable bidirectional plan."""

    if plan.mode is not TerminologySyncMode.BIDIRECTIONAL:
        raise ValueError("inbound change sets require a bidirectional plan")
    if plan.blocked or not remote_snapshot.stable:
        raise ValueError("blocked or unstable remote input cannot produce an inbound change set")
    if plan.remote_snapshot_digest != remote_snapshot.observed_digest:
        raise ValueError("remote snapshot no longer matches the source plan")
    if not source_run_id.strip():
        raise ValueError("source run ID must not be empty")
    created_at = _aware(created_at, "change set created_at").astimezone(UTC)
    remote_by_id = {item.remote_id: item for item in remote_snapshot.items}
    items = tuple(
        _inbound_item(item, remote_by_id.get(item.remote_id))
        for item in plan.items
        if item.action
        in {
            TerminologySyncAction.PROPOSE_LOCAL_ADD,
            TerminologySyncAction.PROPOSE_LOCAL_UPDATE,
            TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION,
            TerminologySyncAction.CONFLICT,
        }
        and item.remote_id is not None
    )
    if not items:
        raise ValueError("bidirectional plan contains no inbound review items")
    change_set_id = _change_set_identity(
        plan.line_id,
        plan.baseline_revision,
        remote_snapshot.observed_digest,
        items,
    )
    content_payload = {
        "change_set_id": change_set_id,
        "revision": 0,
        "line_id": plan.line_id,
        "project_id": plan.local_project_id,
        "variant_id": plan.local_variant_id,
        "target_identity": plan.target_identity,
        "remote_project_id": remote_snapshot.project_id,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "baseline_revision": plan.baseline_revision,
        "remote_snapshot_digest": remote_snapshot.observed_digest,
        "source_run_id": source_run_id,
        "source_run_outcome": TerminologySyncRunOutcome(source_run_outcome).value,
        "items": [item.canonical_payload() for item in sorted(items, key=lambda value: value.item_id)],
        "created_at": created_at.isoformat(),
    }
    return InboundTerminologyChangeSet(
        change_set_id=change_set_id,
        revision=0,
        line_id=plan.line_id,
        project_id=plan.local_project_id,
        variant_id=plan.local_variant_id,
        target_identity=plan.target_identity,
        remote_project_id=remote_snapshot.project_id,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        baseline_revision=plan.baseline_revision,
        remote_snapshot_digest=remote_snapshot.observed_digest,
        source_run_id=source_run_id,
        source_run_outcome=source_run_outcome,
        items=items,
        content_digest=canonical_digest(
            content_payload,
            namespace="terminology-sync.inbound-set-content.v1",
        ),
        created_at=created_at,
    )


def _inbound_item(plan_item: TerminologySyncPlanItem, remote_term: object | None) -> InboundTerminologyChange:
    from transbridge.application.ports.paratranz_terms import ParaTranzTerm

    kind, effect = {
        TerminologySyncAction.PROPOSE_LOCAL_ADD: (InboundChangeKind.REMOTE_ADD, InboundProposedEffect.ADD),
        TerminologySyncAction.PROPOSE_LOCAL_UPDATE: (InboundChangeKind.REMOTE_UPDATE, InboundProposedEffect.UPDATE),
        TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION: (
            InboundChangeKind.REMOTE_DELETE,
            InboundProposedEffect.SUPPRESS,
        ),
        TerminologySyncAction.CONFLICT: (InboundChangeKind.REMOTE_CONFLICT, InboundProposedEffect.CONFLICT),
    }[plan_item.action]
    if plan_item.remote_id is None:
        raise ValueError("inbound plan item requires a remote ID")
    remote = remote_term if isinstance(remote_term, ParaTranzTerm) else None
    return InboundTerminologyChange(
        item_id=plan_item.item_id,
        kind=kind,
        remote_id=plan_item.remote_id,
        remote_revision=None if remote is None else remote.server_revision,
        remote_observed_digest=None if remote is None else remote.observed_digest,
        base_digest=plan_item.base_digest,
        local_term_id=plan_item.local_term_id,
        local=plan_item.local,
        remote=plan_item.remote,
        proposed_effect=effect,
        reason=plan_item.reason.value,
    )


def _change_set_identity(
    line_id: str,
    baseline_revision: int | None,
    remote_snapshot_digest: str,
    items: tuple[InboundTerminologyChange, ...],
) -> str:
    return canonical_digest(
        {
            "line_id": line_id,
            "baseline_revision": baseline_revision,
            "remote_snapshot_digest": remote_snapshot_digest,
            "items": [item.canonical_payload() for item in sorted(items, key=lambda value: value.item_id)],
        },
        namespace="terminology-sync.inbound-set-id.v1",
    )


def inbound_review_identity(change_set: InboundTerminologyChangeSet) -> str:
    """Identify the same unresolved remote/local facts across later sync runs."""

    items = []
    for item in sorted(change_set.items, key=lambda value: (value.remote_id, value.kind.value)):
        payload = item.canonical_payload()
        # These fields bind one planner/baseline observation, not the review
        # fact the user must resolve. Repeated pulls of unchanged facts reuse
        # the original immutable set and its review history.
        for key in ("item_id", "content_digest", "base_digest"):
            payload.pop(key, None)
        items.append(payload)
    return canonical_digest(
        {
            "line_id": change_set.line_id,
            "target_identity": change_set.target_identity,
            "remote_snapshot_digest": change_set.remote_snapshot_digest,
            "items": items,
        },
        namespace="terminology-sync.inbound-review-identity.v1",
    )


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _optional_required(value: str | None, label: str) -> None:
    if value is not None:
        _required(value, label)


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _revision(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


__all__ = [
    "InboundAppliedProposal",
    "InboundChangeKind",
    "InboundChangeSetStorePort",
    "InboundItemDisposition",
    "InboundProposedEffect",
    "InboundReviewDecision",
    "InboundReviewState",
    "InboundReviewStatus",
    "InboundTerminologyChange",
    "InboundTerminologyChangeSet",
    "inbound_review_identity",
    "build_inbound_change_set",
]
