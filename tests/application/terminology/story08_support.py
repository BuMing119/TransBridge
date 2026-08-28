from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.terminology.models import (
    BuildResult,
    BuildResultRef,
    BuildSummary,
    DecisionStatus,
    DraftRef,
    TermDecision,
    TerminologyDraft,
)
from transbridge.application.terminology.workloads import TerminologyExpectedState


def build() -> BuildResult:
    return BuildResult(
        BuildResultRef("build:s08", "build-content:s08"),
        "project-1",
        "variant-1",
        BuildSummary(1, 0, 0, 0),
    )


def decision(term_id: str = "term-1", *, translation: str = "龙") -> TermDecision:
    return TermDecision(
        term_id,
        "project-1",
        "variant-1",
        "Dragon",
        "dragon",
        translation,
        status=DecisionStatus.MANUAL_CONFIRMED,
        evidence_ids=("evidence-1",),
    )


def draft() -> TerminologyDraft:
    ref = DraftRef("draft-1", "project-1", "variant-1", None, "no-base", 0, "decision-set-0")
    return TerminologyDraft(ref, (decision(),))


def expected(*, draft_id: str = "draft-1", draft_revision: int = 0) -> TerminologyExpectedState:
    return TerminologyExpectedState(
        7,
        3,
        "source-graph",
        "source-fingerprints",
        effective_version_id=None,
        base_version_id=None,
        draft_id=draft_id,
        draft_revision=draft_revision,
        build_freshness_digest="current",
    )


@dataclass
class State:
    value: TerminologyExpectedState

    def current(self, project_id: str, variant_id: str) -> TerminologyExpectedState:
        assert (project_id, variant_id) == ("project-1", "variant-1")
        return self.value


@dataclass
class Permit:
    allowed: bool = True

    def is_permitted(self) -> bool:
        return self.allowed
