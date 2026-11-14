# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read-only workbench summaries, explicitly distinct from complete domain snapshots."""

from dataclasses import dataclass

from .conflict_review import TerminologyReviewCounts
from .models import (
    BuildCompleteness,
    BuildFreshness,
    BuildResultRef,
    BuildSummary,
    DraftRef,
    LlmExtractionStatus,
    TerminologyVersionRef,
)


@dataclass(frozen=True, slots=True)
class BuildStatus:
    ref: BuildResultRef
    summary: BuildSummary
    completeness: BuildCompleteness
    freshness: BuildFreshness
    llm_status: LlmExtractionStatus
    diagnostics: tuple[str, ...]
    direct_count: int
    extracted_count: int


@dataclass(frozen=True, slots=True)
class DraftStatus:
    ref: DraftRef
    decision_count: int
    added_count: int
    unchanged_count: int


@dataclass(frozen=True, slots=True)
class WorkbenchStatus:
    build: BuildStatus | None
    draft: DraftStatus | None
    version_ref: TerminologyVersionRef | None
    review_counts: TerminologyReviewCounts
