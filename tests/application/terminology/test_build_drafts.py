from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.terminology.story07_support import DraftTransactions, line
from tests.application.terminology.test_reducer import _candidate
from transbridge.application.terminology.build_drafts import BuildDraftCoordinator, decisions_from_build
from transbridge.application.terminology.drafts import new_draft
from transbridge.application.terminology.errors import RevisionConflictError
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import (
    BuildResult,
    BuildResultRef,
    BuildSummary,
    ConflictStatus,
    DecisionStatus,
    TermDecision,
    TermScope,
)
from transbridge.application.terminology.reducer import CanonicalTerminologyReducer


def _decision(original, translation, **values):
    scope = values.pop("scope", TermScope.project())
    return TermDecision(
        term_id(project_id="project-1", variant_id="variant-1", original=original, scope=scope),
        "project-1",
        "variant-1",
        original,
        normalize_original(original),
        translation,
        scope=scope,
        status=DecisionStatus.MANUAL_CONFIRMED,
        **values,
    )


def _build(*candidates):
    reduced = CanonicalTerminologyReducer().reduce(
        project_id="project-1", variant_id="variant-1", candidates=candidates
    )
    return BuildResult(
        BuildResultRef("build", "digest"),
        "project-1",
        "variant-1",
        BuildSummary(1, 0, len(reduced.candidates), len(reduced.conflicts)),
        candidates=reduced.candidates,
        conflicts=reduced.conflicts,
    )


def _reconcile(build, *, decisions=(), conflicts=(), current=None, transactions=None):
    current_line = line()
    transactions = transactions or DraftTransactions(current_line)
    transactions.draft = current
    return BuildDraftCoordinator(transactions).reconcile(
        build,
        line=current_line,
        draft_id="new-draft",
        current=current,
        effective=SimpleNamespace(decisions=decisions, conflicts=conflicts),
    )


def test_nonempty_candidates_become_adopted_decisions_with_canonical_identity():
    result = decisions_from_build(_build(_candidate("Dragon", "龙", "e1")))

    assert len(result) == 1
    assert result[0].term_id == _decision("Dragon", "龙").term_id
    assert result[0].status is DecisionStatus.ADOPTED


def test_rebuild_preserves_manual_values_suppression_and_unobserved_terms():
    manual = _decision("Dragon", "龙", variants=("Dragons",), notes="human note", evidence_ids=("old",))
    suppressed = _decision("Sword", "", suppressed=True)
    plugin = _decision("Title", "领主", scope=TermScope.plugin("A.esm"), evidence_ids=("old",))
    result = _reconcile(
        _build(_candidate("Dragon", "龙", "new"), _candidate("Sword", "剑", "sword")),
        decisions=(manual, suppressed, plugin),
    )
    actual = {item.original: item for item in result.decisions}

    assert actual["Dragon"] == replace(manual, evidence_ids=("new",))
    assert actual["Sword"] == replace(suppressed, evidence_ids=("sword",))
    assert actual["Title"] == replace(plugin, evidence_ids=())
    assert result.actions == ()


def test_rebuild_adds_new_terms_to_existing_draft_and_marks_contradictions_for_review():
    manual = _decision("Dragon", "manual")
    current = new_draft(
        draft_id="existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="version-content-1",
        decisions=(manual,),
    )
    result = _reconcile(_build(_candidate("Dragon", "new", "e1"), _candidate("Sword", "剑", "e2")), current=current)
    actual = {item.original: item for item in result.decisions}

    assert result.ref.draft_id == current.ref.draft_id
    assert result.ref.revision == current.ref.revision + 1
    assert actual["Dragon"].translation == "manual"
    assert actual["Dragon"].status is DecisionStatus.REVIEW_REQUIRED
    assert actual["Sword"].status is DecisionStatus.ADOPTED


def test_rebuild_cannot_overwrite_a_concurrently_saved_draft():
    current = new_draft(
        draft_id="existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="version-content-1",
    )

    class ConcurrentTransactions(DraftTransactions):
        def save_draft(self, draft, *, expectation):
            from transbridge.application.terminology.drafts import revised_draft

            self.draft = revised_draft(current, decisions=(_decision("Saved", "user"),))
            return super().save_draft(draft, expectation=expectation)

    transactions = ConcurrentTransactions(line())
    with pytest.raises(RevisionConflictError):
        _reconcile(_build(_candidate("Dragon", "龙", "e1")), current=current, transactions=transactions)
    assert transactions.draft.decisions[0].original == "Saved"


def test_stable_conflict_resolution_survives_rebuild_but_new_translation_requires_review():
    build = _build(_candidate("Dragon", "龙", "e1"), _candidate("Dragon", "巨龙", "e2"))
    resolution = replace(build.conflicts[0], status=ConflictStatus.UNIFIED, recommended_translation="龙")
    decision = _decision("Dragon", "龙")
    result = _reconcile(build, decisions=(decision,), conflicts=(resolution,))

    assert result.decisions[0].status is DecisionStatus.MANUAL_CONFIRMED
    assert result.conflict_resolutions[0].status is ConflictStatus.UNIFIED
    changed = _build(*build.candidates, _candidate("Dragon", "飞龙", "e3"))
    updated = _reconcile(changed, decisions=(decision,), conflicts=(resolution,))
    assert updated.decisions[0].status is DecisionStatus.REVIEW_REQUIRED
    assert updated.conflict_resolutions == ()


def test_obsolete_resolutions_are_removed_when_conflict_disappears():
    build = _build(_candidate("Dragon", "龙", "e1"), _candidate("Dragon", "巨龙", "e2"))
    resolution = replace(build.conflicts[0], status=ConflictStatus.UNIFIED, recommended_translation="龙")
    result = _reconcile(_build(), decisions=(_decision("Dragon", "龙"),), conflicts=(resolution,))

    assert result.conflict_resolutions == ()
    assert result.decisions[0].evidence_ids == ()
    assert result.decisions[0].is_effective
