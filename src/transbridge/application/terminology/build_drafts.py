"""Merge a new build into review state without replacing human decisions."""

from __future__ import annotations

from dataclasses import replace

from .conflicts import reconcile_evidence
from .drafts import DraftLineState, DraftTransactionPort, DraftWriteExpectation, new_draft, revised_draft
from .errors import RepositoryConflictError
from .identity import normalize_translation, term_id
from .models import (
    BuildResult,
    ConflictGroup,
    ConflictStatus,
    DecisionStatus,
    ScopeKind,
    TermDecision,
    TerminologyDraft,
    TerminologyVersion,
)
from .reducer import ManualBaselineReconciler


def decisions_from_build(build: BuildResult, baseline: tuple[TermDecision, ...] = ()) -> tuple[TermDecision, ...]:
    candidates = ManualBaselineReconciler().reconcile(build.candidates, baseline, build.conflicts).effective_candidates
    return tuple(
        TermDecision(
            term_id(
                project_id=build.project_id,
                variant_id=build.variant_id,
                scope=candidate.scope,
                original=candidate.original,
            ),
            build.project_id,
            build.variant_id,
            candidate.original,
            candidate.normalized_original,
            candidate.translation,
            scope=candidate.scope,
            status=DecisionStatus.ADOPTED,
            evidence_ids=candidate.evidence_ids,
        )
        for candidate in candidates
    )


class BuildDraftCoordinator:
    def __init__(self, transactions: DraftTransactionPort) -> None:
        self._transactions = transactions

    def reconcile(
        self,
        build: BuildResult,
        *,
        line: DraftLineState,
        draft_id: str,
        current: TerminologyDraft | None,
        effective: TerminologyVersion | None,
    ) -> TerminologyDraft:
        if (build.project_id, build.variant_id) != (line.project_id, line.variant_id):
            raise RepositoryConflictError("build does not belong to the current draft line")
        baseline = current if current is not None else effective
        decisions = () if baseline is None else baseline.decisions
        previous_conflicts = (
            current.conflict_resolutions if current is not None else (() if effective is None else effective.conflicts)
        )
        resolutions = _current_resolutions(previous_conflicts, build.conflicts)
        reconciled, _ = reconcile_evidence(decisions, build.candidates)
        resolved = {item.normalized_original: item for item in resolutions}
        originals = {item.term_id: item for item in decisions}
        reconciled = tuple(
            replace(item, status=originals[item.term_id].status)
            if _reviewed_evidence_preserved(originals[item.term_id], item)
            or _resolution_applies(resolved.get(item.normalized_original), item)
            else item
            for item in reconciled
        )
        merged = (*reconciled, *decisions_from_build(build, decisions))
        if current is None:
            draft = new_draft(
                draft_id=draft_id,
                project_id=line.project_id,
                variant_id=line.variant_id,
                base_version_id=line.effective_version_id,
                base_content_digest=line.effective_content_digest,
                decisions=merged,
                conflict_resolutions=resolutions,
            )
            self._transactions.create_draft(draft, expected_line=line, historical_base=False)
            return draft
        if (current.ref.base_version_id, current.ref.base_content_digest) != (
            line.effective_version_id,
            line.effective_content_digest,
        ):
            raise RepositoryConflictError("术语草稿的基线已经变化，请重新基于当前版本建立草稿。")
        draft = revised_draft(
            current,
            decisions=merged,
            conflict_resolutions=resolutions,
            digest_context={"build_ref": build.ref},
        )
        self._transactions.save_draft(draft, expectation=DraftWriteExpectation.from_draft(current, line))
        return draft


def _current_resolutions(
    previous: tuple[ConflictGroup, ...], current: tuple[ConflictGroup, ...]
) -> tuple[ConflictGroup, ...]:
    by_id = {item.conflict_group_id: item for item in previous if item.status is not ConflictStatus.UNRESOLVED}
    return tuple(
        replace(item, status=old.status, recommended_translation=old.recommended_translation, notes=old.notes)
        for item in current
        if (old := by_id.get(item.conflict_group_id)) is not None
        and tuple(variant.normalized_translation for variant in old.variants)
        == tuple(variant.normalized_translation for variant in item.variants)
    )


def _resolution_applies(conflict: ConflictGroup | None, decision: TermDecision) -> bool:
    if conflict is None or conflict.recommended_translation is None:
        return False
    return (
        conflict.status is ConflictStatus.UNIFIED
        or conflict.status is ConflictStatus.PLUGIN_EXCEPTION
        and decision.scope.kind is ScopeKind.PLUGIN
    ) and normalize_translation(decision.translation) == normalize_translation(conflict.recommended_translation)


def _reviewed_evidence_preserved(previous: TermDecision, current: TermDecision) -> bool:
    return previous.status is DecisionStatus.MANUAL_CONFIRMED and set(current.evidence_ids).issubset(
        previous.evidence_ids
    )


__all__ = ["BuildDraftCoordinator", "decisions_from_build"]
