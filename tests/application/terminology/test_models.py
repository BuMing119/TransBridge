from dataclasses import FrozenInstanceError

import pytest

from transbridge.application.terminology.errors import StaleBuildError
from transbridge.application.terminology.models import (
    BuildFreshness,
    BuildResult,
    BuildResultRef,
    BuildSummary,
    DecisionStatus,
    DraftRef,
    ManualAction,
    ManualActionType,
    TermDecision,
    TerminologyDraft,
    TermScope,
)


def _decision(*, status: DecisionStatus = DecisionStatus.ADOPTED, suppressed: bool = False) -> TermDecision:
    return TermDecision(
        term_id="term-1",
        project_id="project-1",
        variant_id="variant-1",
        original="Dragon",
        normalized_original="dragon",
        translation="龙",
        status=status,
        suppressed=suppressed,
    )


def test_models_are_frozen_and_collection_fields_are_canonical() -> None:
    decision = TermDecision(
        term_id="term-1",
        project_id="project-1",
        variant_id="variant-1",
        original="Dragon",
        normalized_original="dragon",
        translation="龙",
        variants=("z", "a"),
        evidence_ids=("e-2", "e-1"),
    )

    assert decision.variants == ("a", "z")
    assert decision.evidence_ids == ("e-1", "e-2")
    with pytest.raises(FrozenInstanceError):
        decision.translation = "巨龙"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("status", "suppressed"),
    [
        (DecisionStatus.UNRESOLVED, False),
        (DecisionStatus.REVIEW_REQUIRED, False),
        (DecisionStatus.ADOPTED, True),
    ],
)
def test_non_effective_decisions_are_rejected_from_effective_projection(
    status: DecisionStatus, suppressed: bool
) -> None:
    with pytest.raises(ValueError, match="cannot enter"):
        _decision(status=status, suppressed=suppressed).require_effective()


def test_manual_action_requires_a_non_empty_actor() -> None:
    with pytest.raises(ValueError, match="actor"):
        ManualAction(
            action_id="action-1",
            term_id="term-1",
            action_type=ManualActionType.ADD,
            actor="  ",
            occurred_at="2026-08-28T00:00:00Z",
            base_version_id=None,
            before_digest=None,
            after_digest="after",
        )


def test_stale_build_cannot_be_published() -> None:
    result = BuildResult(
        BuildResultRef("build:v1:one", "content:v1:one"),
        "project-1",
        "variant-1",
        BuildSummary(0, 0, 0, 0),
        freshness=BuildFreshness.STALE,
    )

    with pytest.raises(StaleBuildError):
        result.require_publishable()


def test_draft_cache_identity_includes_every_manual_baseline_dimension() -> None:
    first = DraftRef("draft-a", "project-1", "variant-1", "v1", "base-a", 1, "decisions-a")
    rebuilt = DraftRef("draft-b", "project-1", "variant-1", "v1", "base-a", 1, "decisions-a")
    changed = DraftRef("draft-a", "project-1", "variant-1", "v1", "base-a", 1, "decisions-b")

    assert first.cache_identity != rebuilt.cache_identity
    assert first.cache_identity != changed.cache_identity
    assert TerminologyDraft(first).ref.cache_identity == first.cache_identity


def test_scope_distinguishes_project_and_plugin_lines() -> None:
    assert TermScope.project().canonical_key == "project"
    assert TermScope.plugin("Skyrim.esm").canonical_key == "plugin:Skyrim.esm"
    with pytest.raises(ValueError, match="plugin ID"):
        TermScope.plugin(" ")
