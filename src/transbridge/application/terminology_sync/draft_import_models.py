"""Immutable contracts for the inbound draft-import workflow."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from transbridge.application.terminology.drafts import DraftLineState, DraftWriteExpectation
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import DraftRef, ManualActionType, TermDecision

from .inbound import InboundReviewDecision, InboundReviewState, InboundReviewStatus
from .plan_models import TerminologyContentSummary


class DraftImportEffect(StrEnum):
    ADD = "add"
    UPDATE = "update"
    SUPPRESS = "suppress"
    REJECT = "reject"
    CONFLICT = "conflict"
    NO_CHANGE = "no_change"


class DraftImportStaleError(RuntimeError):
    code = "INBOUND_DRAFT_IMPORT_STALE"


class DraftImportStatePort(Protocol):
    def current_line(self, project_id: str, variant_id: str) -> DraftLineState: ...

    def effective_decisions(self, line: DraftLineState) -> tuple[TermDecision, ...]: ...


@dataclass(frozen=True, slots=True)
class DraftImportChoice:
    item_id: str
    decision: InboundReviewDecision
    edited: TerminologyContentSummary | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("draft import choice item ID must not be empty")
        decision = InboundReviewDecision(self.decision)
        object.__setattr__(self, "decision", decision)
        if decision is InboundReviewDecision.EDIT and self.edited is None:
            raise ValueError("edit choice requires edited terminology content")
        if decision is not InboundReviewDecision.EDIT and self.edited is not None:
            raise ValueError("only edit choice may carry edited terminology content")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("draft import choice reason must be absent or non-empty")


@dataclass(frozen=True, slots=True)
class DraftImportSelection:
    change_set_id: str
    change_set_content_digest: str
    expected_review_revision: int
    expected_line: DraftLineState
    choices: tuple[DraftImportChoice, ...]
    draft_expectation: DraftWriteExpectation | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.change_set_id, "selection change set ID"),
            (self.change_set_content_digest, "selection change set digest"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must not be empty")
        if (
            isinstance(self.expected_review_revision, bool)
            or not isinstance(self.expected_review_revision, int)
            or self.expected_review_revision < 0
        ):
            raise ValueError("expected review revision must be a non-negative integer")
        choices = tuple(sorted(self.choices, key=lambda item: item.item_id))
        if not choices:
            raise ValueError("draft import selection requires at least one choice")
        if len({item.item_id for item in choices}) != len(choices):
            raise ValueError("draft import choice item IDs must be unique")
        object.__setattr__(self, "choices", choices)


@dataclass(frozen=True, slots=True)
class DraftImportMutation:
    item_id: str
    effect: DraftImportEffect
    review_status: InboundReviewStatus
    before: TermDecision | None
    after: TermDecision | None
    action_type: ManualActionType | None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("draft import mutation item ID must not be empty")
        object.__setattr__(self, "effect", DraftImportEffect(self.effect))
        status = InboundReviewStatus(self.review_status)
        if status not in {
            InboundReviewStatus.ACCEPTED,
            InboundReviewStatus.REJECTED,
            InboundReviewStatus.EDITED,
            InboundReviewStatus.CONFLICT,
        }:
            raise ValueError("draft import mutation requires a terminal item review status")
        object.__setattr__(self, "review_status", status)
        if self.action_type is not None:
            object.__setattr__(self, "action_type", ManualActionType(self.action_type))
        if self.diagnostic is not None and not self.diagnostic.strip():
            raise ValueError("draft import diagnostic must be absent or non-empty")


@dataclass(frozen=True, slots=True)
class DraftImportProposal:
    selection: DraftImportSelection
    source_review_revision: int
    initial_decision_digest: str
    mutations: tuple[DraftImportMutation, ...]
    decisions: tuple[TermDecision, ...]
    counts: tuple[tuple[str, int], ...]
    diagnostics: tuple[str, ...]
    proposal_digest: str

    def __post_init__(self) -> None:
        if self.source_review_revision != self.selection.expected_review_revision:
            raise ValueError("proposal review revision must match its selection")
        if not self.initial_decision_digest.strip() or not self.proposal_digest.strip():
            raise ValueError("proposal digests must not be empty")
        mutations = tuple(sorted(self.mutations, key=lambda item: item.item_id))
        if len(mutations) != len(self.selection.choices):
            raise ValueError("proposal must contain one mutation per selected item")
        object.__setattr__(self, "mutations", mutations)
        object.__setattr__(self, "decisions", tuple(sorted(self.decisions, key=lambda item: item.term_id)))
        object.__setattr__(self, "counts", tuple(sorted(self.counts)))
        object.__setattr__(self, "diagnostics", tuple(sorted(set(self.diagnostics))))
        expected_counts = tuple(sorted(Counter(item.effect.value for item in mutations).items()))
        if self.counts != expected_counts:
            raise ValueError("proposal counts do not match its mutations")
        expected_digest = canonical_digest(
            {
                "selection": self.selection,
                "source_review_revision": self.source_review_revision,
                "initial_decision_digest": self.initial_decision_digest,
                "mutations": self.mutations,
                "decisions": self.decisions,
                "diagnostics": self.diagnostics,
            },
            namespace="terminology-sync.draft-import-proposal.v1",
        )
        if self.proposal_digest != expected_digest:
            raise ValueError("proposal digest does not match its preview content")

    @property
    def committable(self) -> bool:
        return all(item.review_status is not InboundReviewStatus.CONFLICT for item in self.mutations)


@dataclass(frozen=True, slots=True)
class DraftImportCommitResult:
    proposal_digest: str
    draft_ref: DraftRef | None
    review_state: InboundReviewState
    replayed: bool = False
    reconciled: bool = False


__all__ = [
    "DraftImportChoice",
    "DraftImportCommitResult",
    "DraftImportEffect",
    "DraftImportMutation",
    "DraftImportProposal",
    "DraftImportSelection",
    "DraftImportStaleError",
    "DraftImportStatePort",
]
