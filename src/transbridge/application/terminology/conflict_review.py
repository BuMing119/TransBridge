"""Complete, immutable evidence for a conflict review and its guarded decision."""

from dataclasses import dataclass

from .models import BilingualEvidence, BuildResultRef, ConflictGroup, DraftRef, TermCandidate, TermDecision


@dataclass(frozen=True, slots=True)
class TerminologyConflictDetails:
    build_ref: BuildResultRef
    draft_ref: DraftRef
    conflict: ConflictGroup
    candidates: tuple[TermCandidate, ...]
    evidence: tuple[BilingualEvidence, ...]
    plugin_ids: tuple[str, ...]
    decisions: tuple[TermDecision, ...]


@dataclass(frozen=True, slots=True)
class TerminologyReviewCounts:
    review_required_terms: int = 0
    unresolved_conflicts: int = 0

    @property
    def total(self) -> int:
        return self.review_required_terms + self.unresolved_conflicts


__all__ = ["TerminologyConflictDetails", "TerminologyReviewCounts"]
