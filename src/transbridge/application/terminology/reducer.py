"""Canonical terminology reduction, conflict grouping, and baseline reconciliation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .identity import candidate_id, conflict_group_id
from .models import (
    BilingualEvidence,
    ConflictGroup,
    ConflictRisk,
    ConflictStatus,
    ConflictVariant,
    DecisionStatus,
    ExtractionMethod,
    LlmExtractionStatus,
    TermCandidate,
    TermDecision,
)


@dataclass(frozen=True, slots=True)
class ReductionResult:
    candidates: tuple[TermCandidate, ...]
    conflicts: tuple[ConflictGroup, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    effective_candidates: tuple[TermCandidate, ...]
    preserved_decisions: tuple[TermDecision, ...]
    review_term_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LogicalTerminologyFragment:
    component_id: str
    evidence: tuple[BilingualEvidence, ...]
    candidates: tuple[TermCandidate, ...]
    excluded_reasons: tuple[tuple[str, int], ...]
    diagnostics: tuple[str, ...]
    llm_status: LlmExtractionStatus


@dataclass(frozen=True, slots=True)
class GlobalReductionResult:
    evidence: tuple[BilingualEvidence, ...]
    candidates: tuple[TermCandidate, ...]
    conflicts: tuple[ConflictGroup, ...]
    excluded_reasons: tuple[tuple[str, int], ...]
    diagnostics: tuple[str, ...]
    llm_status: LlmExtractionStatus
    reconciliation: ReconciliationResult


class CanonicalTerminologyReducer:
    def reduce(
        self,
        *,
        project_id: str,
        variant_id: str,
        candidates: tuple[TermCandidate, ...],
    ) -> ReductionResult:
        merged = self._merge_same_terms(candidates)
        by_original: dict[str, list[TermCandidate]] = {}
        for candidate in merged:
            by_original.setdefault(candidate.normalized_original, []).append(candidate)

        conflicts: list[ConflictGroup] = []
        for normalized_original, original_candidates in sorted(by_original.items()):
            by_translation: dict[str, list[TermCandidate]] = {}
            for candidate in original_candidates:
                by_translation.setdefault(candidate.normalized_translation, []).append(candidate)
            if len(by_translation) < 2:
                continue
            variants = tuple(
                ConflictVariant(
                    normalized_translation=translation,
                    candidate_ids=tuple(item.candidate_id for item in values),
                    evidence_ids=tuple(sorted({evidence_id for item in values for evidence_id in item.evidence_ids})),
                )
                for translation, values in sorted(by_translation.items())
            )
            conflicts.append(
                ConflictGroup(
                    conflict_group_id=conflict_group_id(
                        project_id=project_id,
                        variant_id=variant_id,
                        original=normalized_original,
                    ),
                    project_id=project_id,
                    variant_id=variant_id,
                    normalized_original=normalized_original,
                    variants=variants,
                    risk=ConflictRisk.HIGH if len(variants) >= 3 else ConflictRisk.MEDIUM,
                )
            )
        return ReductionResult(
            tuple(sorted(merged, key=_candidate_sort_key)),
            tuple(sorted(conflicts, key=lambda item: (item.normalized_original, item.conflict_group_id))),
        )

    @staticmethod
    def _merge_same_terms(candidates: tuple[TermCandidate, ...]) -> tuple[TermCandidate, ...]:
        groups: dict[tuple[str, str, str], list[TermCandidate]] = {}
        for candidate in candidates:
            key = (
                candidate.normalized_original,
                candidate.scope.canonical_key,
                candidate.normalized_translation,
            )
            groups.setdefault(key, []).append(candidate)
        merged: list[TermCandidate] = []
        for values in groups.values():
            ordered = sorted(values, key=_candidate_sort_key)
            display = min(ordered, key=lambda item: (item.original, item.translation, item.candidate_id))
            evidence_ids = tuple(sorted({evidence for item in ordered for evidence in item.evidence_ids}))
            method = min((item.extraction_method for item in ordered), key=_method_priority)
            algorithm_version = "+".join(sorted({item.algorithm_version for item in ordered}))
            identity = candidate_id(
                evidence_ids=evidence_ids,
                original=display.original,
                translation=display.translation,
                scope=display.scope,
                extraction_method=method,
                algorithm_version=algorithm_version,
            )
            merged.append(
                TermCandidate(
                    identity,
                    display.original,
                    display.translation,
                    display.normalized_original,
                    display.normalized_translation,
                    evidence_ids,
                    display.scope,
                    method,
                    algorithm_version,
                )
            )
        return tuple(merged)


class ManualBaselineReconciler:
    """Keep manual facts authoritative and surface new automatic contradictions."""

    def reconcile(
        self,
        candidates: tuple[TermCandidate, ...],
        decisions: tuple[TermDecision, ...],
        conflicts: tuple[ConflictGroup, ...] = (),
    ) -> ReconciliationResult:
        decision_map: dict[tuple[str, str], TermDecision] = {}
        for item in sorted(decisions, key=lambda item: item.term_id):
            key = (item.normalized_original, item.scope.canonical_key)
            if key in decision_map:
                raise ValueError("manual baseline contains duplicate decisions for one term scope")
            decision_map[key] = item
        unresolved_originals = {
            item.normalized_original for item in conflicts if item.status is ConflictStatus.UNRESOLVED
        }
        effective: list[TermCandidate] = []
        review: set[str] = set()
        diagnostics: list[str] = []
        for candidate in sorted(candidates, key=_candidate_sort_key):
            if candidate.normalized_original in unresolved_originals:
                diagnostics.append(f"AUTOMATIC_CONFLICT_NOT_EFFECTIVE:{candidate.candidate_id}")
                continue
            decision = decision_map.get((candidate.normalized_original, candidate.scope.canonical_key))
            if decision is None:
                effective.append(candidate)
                continue
            if decision.suppressed:
                diagnostics.append(f"MANUAL_SUPPRESSION_PRESERVED:{decision.term_id}")
                continue
            if decision.status not in {DecisionStatus.ADOPTED, DecisionStatus.MANUAL_CONFIRMED}:
                diagnostics.append(f"MANUAL_DECISION_NOT_EFFECTIVE:{decision.term_id}")
                continue
            if candidate.normalized_translation != _decision_translation(decision):
                review.add(decision.term_id)
                diagnostics.append(f"MANUAL_DECISION_NEW_CONFLICT:{decision.term_id}:{candidate.candidate_id}")
            else:
                diagnostics.append(f"MANUAL_DECISION_CONSISTENT:{decision.term_id}")
        return ReconciliationResult(
            tuple(effective),
            tuple(sorted(decisions, key=lambda item: item.term_id)),
            tuple(sorted(review)),
            tuple(sorted(set(diagnostics))),
        )


def reduce_fragments(
    *,
    project_id: str,
    variant_id: str,
    fragments: tuple[LogicalTerminologyFragment, ...],
    baseline_decisions: tuple[TermDecision, ...] = (),
    reducer: CanonicalTerminologyReducer | None = None,
    reconciler: ManualBaselineReconciler | None = None,
) -> GlobalReductionResult:
    """Globally reduce a complete logical fragment set for full and incremental builds."""

    evidence = {
        item.evidence_id: item
        for fragment in sorted(fragments, key=lambda item: item.component_id)
        for item in fragment.evidence
    }
    candidates = tuple(item for fragment in fragments for item in fragment.candidates)
    reduction = (reducer or CanonicalTerminologyReducer()).reduce(
        project_id=project_id,
        variant_id=variant_id,
        candidates=candidates,
    )
    reconciliation = (reconciler or ManualBaselineReconciler()).reconcile(
        reduction.candidates,
        baseline_decisions,
        reduction.conflicts,
    )
    excluded: Counter[str] = Counter()
    diagnostics: set[str] = set(reconciliation.diagnostics)
    statuses: list[LlmExtractionStatus] = []
    for fragment in fragments:
        excluded.update(dict(fragment.excluded_reasons))
        diagnostics.update(fragment.diagnostics)
        statuses.append(fragment.llm_status)
    return GlobalReductionResult(
        tuple(sorted(evidence.values(), key=lambda item: item.evidence_id)),
        reduction.candidates,
        reduction.conflicts,
        tuple(sorted(excluded.items())),
        tuple(sorted(diagnostics)),
        _combine_llm_status(statuses),
        reconciliation,
    )


def _combine_llm_status(statuses: list[LlmExtractionStatus]) -> LlmExtractionStatus:
    if any(item is LlmExtractionStatus.PARTIAL for item in statuses):
        return LlmExtractionStatus.PARTIAL
    if any(item is LlmExtractionStatus.PERFORMED for item in statuses):
        return LlmExtractionStatus.PERFORMED
    if any(item is LlmExtractionStatus.UNAVAILABLE for item in statuses):
        return LlmExtractionStatus.UNAVAILABLE
    return LlmExtractionStatus.SKIPPED


def _decision_translation(decision: TermDecision) -> str:
    from .identity import normalize_translation

    return normalize_translation(decision.translation)


def _method_priority(method: ExtractionMethod) -> int:
    return {
        ExtractionMethod.MANUAL: 0,
        ExtractionMethod.DETERMINISTIC_NAME: 1,
        ExtractionMethod.LLM_TEXT: 2,
    }[method]


def _candidate_sort_key(candidate: TermCandidate) -> tuple[str, str, str, str]:
    return (
        candidate.normalized_original,
        candidate.scope.canonical_key,
        candidate.normalized_translation,
        candidate.candidate_id,
    )


__all__ = [
    "CanonicalTerminologyReducer",
    "GlobalReductionResult",
    "LogicalTerminologyFragment",
    "ManualBaselineReconciler",
    "ReconciliationResult",
    "ReductionResult",
    "reduce_fragments",
]
