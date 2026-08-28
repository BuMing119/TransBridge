"""Conflict decisions and automatic evidence reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from transbridge.application.contracts import RequestContext

from .decisions import DecisionCommand, DecisionOperation, DecisionService
from .drafts import DraftService, DraftWriteExpectation, revised_draft
from .identity import normalize_translation
from .models import (
    ConflictGroup,
    ConflictStatus,
    DecisionStatus,
    ScopeKind,
    TermCandidate,
    TermDecision,
    TerminologyDraft,
    TermScope,
)


class ConflictResolutionOperation(StrEnum):
    UNIFY = "unify"
    PLUGIN_EXCEPTION = "plugin_exception"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConflictResolutionCommand:
    operation: ConflictResolutionOperation
    conflict: ConflictGroup
    expectation: DraftWriteExpectation
    translation: str | None = None
    plugin_id: str | None = None
    term_id: str | None = None
    notes: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", ConflictResolutionOperation(self.operation))
        for name in ("translation", "plugin_id", "term_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name.replace('_', ' ')} must be absent or non-empty")


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    draft: TerminologyDraft
    conflict: ConflictGroup


class ConflictService:
    def __init__(self, decisions: DecisionService) -> None:
        self._decisions = decisions

    def resolve(self, command: ConflictResolutionCommand, context: RequestContext) -> ConflictResolution:
        conflict = command.conflict
        if (conflict.project_id, conflict.variant_id) != (
            command.expectation.line.project_id,
            command.expectation.line.variant_id,
        ):
            raise ValueError("conflict does not belong to the expected Project/Variant draft")
        if command.operation is ConflictResolutionOperation.UNIFY:
            decision_command = DecisionCommand(
                operation=DecisionOperation.UNIFY_TRANSLATION,
                expectation=command.expectation,
                term_id=command.term_id,
                original=conflict.normalized_original,
                translation=_required(command.translation, "unified translation"),
                notes=command.notes,
                reason=command.reason,
            )
            projected = replace(
                conflict,
                status=ConflictStatus.UNIFIED,
                recommended_translation=command.translation,
                notes=command.notes or conflict.notes,
            )
        elif command.operation is ConflictResolutionOperation.PLUGIN_EXCEPTION:
            plugin_id = _required(command.plugin_id, "plugin ID")
            decision_command = DecisionCommand(
                operation=DecisionOperation.PLUGIN_EXCEPTION,
                expectation=command.expectation,
                original=conflict.normalized_original,
                translation=_required(command.translation, "plugin translation"),
                scope=TermScope(ScopeKind.PLUGIN, plugin_id),
                notes=command.notes,
                reason=command.reason,
            )
            projected = replace(
                conflict,
                status=ConflictStatus.PLUGIN_EXCEPTION,
                recommended_translation=command.translation,
                notes=command.notes or conflict.notes,
            )
        else:
            decision_command = DecisionCommand(
                operation=DecisionOperation.IGNORE_CONFLICT,
                expectation=command.expectation,
                term_id=command.term_id,
                original=conflict.normalized_original,
                notes=command.notes,
                reason=command.reason,
            )
            projected = replace(
                conflict,
                status=ConflictStatus.IGNORED,
                recommended_translation=None,
                notes=command.notes or conflict.notes,
            )
        decision_command = replace(decision_command, conflict_resolution=projected)
        draft = self._decisions.apply(decision_command, context)
        return ConflictResolution(draft, projected)


class EvidenceReconciliationStatus(StrEnum):
    ALIGNED = "aligned"
    NEEDS_REVIEW = "needs_review"
    NO_EVIDENCE = "no_evidence"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class DecisionEvidenceReconciliation:
    term_id: str
    status: EvidenceReconciliationStatus
    evidence_ids: tuple[str, ...]
    manual_value_preserved: bool = True
    possibly_stale: bool = False


@dataclass(frozen=True, slots=True)
class ConflictReconciliation:
    draft: TerminologyDraft
    decisions: tuple[DecisionEvidenceReconciliation, ...]
    manual_actions_appended: int = 0

    def __post_init__(self) -> None:
        if self.manual_actions_appended != 0:
            raise ValueError("automatic evidence reconciliation cannot append ManualAction rows")


class EvidenceReconciliationTransactionPort(Protocol):
    """Persist draft evidence/status projections under the same write guard."""

    def save_reconciliation(
        self,
        draft: TerminologyDraft,
        *,
        reconciliation: tuple[DecisionEvidenceReconciliation, ...],
        expectation: DraftWriteExpectation,
    ) -> None: ...


class EvidenceReconciler:
    """Update evidence/status only, preserving every human-authored value."""

    def __init__(
        self,
        drafts: DraftService,
        transactions: EvidenceReconciliationTransactionPort,
    ) -> None:
        self._drafts = drafts
        self._transactions = transactions

    def reconcile(
        self,
        candidates: tuple[TermCandidate, ...],
        *,
        expectation: DraftWriteExpectation,
    ) -> ConflictReconciliation:
        current = self._drafts.active(expectation.line.project_id, expectation.line.variant_id)
        self._drafts.require_expected(current, expectation)
        updated: list[TermDecision] = []
        projected: list[DecisionEvidenceReconciliation] = []
        for decision in current.decisions:
            relevant = tuple(
                candidate
                for candidate in candidates
                if candidate.normalized_original == decision.normalized_original and candidate.scope == decision.scope
            )
            evidence_ids = tuple(sorted({item for candidate in relevant for item in candidate.evidence_ids}))
            if decision.suppressed:
                status = EvidenceReconciliationStatus.SUPPRESSED
                reconciled = replace(decision, evidence_ids=evidence_ids)
                possibly_stale = not evidence_ids
            elif not relevant:
                status = EvidenceReconciliationStatus.NO_EVIDENCE
                reconciled = replace(decision, evidence_ids=())
                possibly_stale = True
            elif any(
                normalize_translation(candidate.translation) != normalize_translation(decision.translation)
                for candidate in relevant
            ):
                status = EvidenceReconciliationStatus.NEEDS_REVIEW
                reconciled = replace(
                    decision,
                    evidence_ids=evidence_ids,
                    status=DecisionStatus.REVIEW_REQUIRED,
                )
                possibly_stale = False
            else:
                status = EvidenceReconciliationStatus.ALIGNED
                reconciled = replace(decision, evidence_ids=evidence_ids)
                possibly_stale = False
            updated.append(reconciled)
            projected.append(
                DecisionEvidenceReconciliation(
                    decision.term_id,
                    status,
                    evidence_ids,
                    possibly_stale=possibly_stale,
                )
            )
        next_draft = revised_draft(
            current,
            decisions=tuple(updated),
            actions=current.actions,
            digest_context={
                "automatic_evidence_reconciliation": tuple(
                    (item.term_id, item.status.value, item.evidence_ids) for item in projected
                )
            },
        )
        reconciliation = tuple(projected)
        self._transactions.save_reconciliation(
            next_draft,
            reconciliation=reconciliation,
            expectation=expectation,
        )
        return ConflictReconciliation(next_draft, reconciliation)


def _required(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


__all__ = [
    "ConflictReconciliation",
    "ConflictResolution",
    "ConflictResolutionCommand",
    "ConflictResolutionOperation",
    "ConflictService",
    "DecisionEvidenceReconciliation",
    "EvidenceReconciler",
    "EvidenceReconciliationTransactionPort",
    "EvidenceReconciliationStatus",
]
