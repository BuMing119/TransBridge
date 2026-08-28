from transbridge.application.terminology.identity import candidate_id, normalize_original, normalize_translation
from transbridge.application.terminology.models import (
    ConflictStatus,
    DecisionStatus,
    ExtractionMethod,
    TermCandidate,
    TermDecision,
    TermScope,
)
from transbridge.application.terminology.reducer import (
    CanonicalTerminologyReducer,
    ManualBaselineReconciler,
)


def _candidate(original: str, translation: str, evidence_id: str, scope: TermScope | None = None):
    resolved_scope = scope or TermScope.project()
    identity = candidate_id(
        evidence_ids=(evidence_id,),
        original=original,
        translation=translation,
        scope=resolved_scope,
        extraction_method=ExtractionMethod.DETERMINISTIC_NAME,
        algorithm_version="v1",
    )
    return TermCandidate(
        identity,
        original,
        translation,
        normalize_original(original),
        normalize_translation(translation),
        (evidence_id,),
        resolved_scope,
        ExtractionMethod.DETERMINISTIC_NAME,
        "v1",
    )


def test_same_normalized_pair_merges_all_evidence_with_stable_order() -> None:
    reducer = CanonicalTerminologyReducer()
    candidates = (
        _candidate("Ｄragon", "龙", "e-2"),
        _candidate("dragon", "龙", "e-1"),
    )

    forward = reducer.reduce(project_id="project-1", variant_id="main", candidates=candidates)
    reverse = reducer.reduce(project_id="project-1", variant_id="main", candidates=tuple(reversed(candidates)))

    assert forward == reverse
    assert len(forward.candidates) == 1
    assert forward.candidates[0].evidence_ids == ("e-1", "e-2")
    assert forward.conflicts == ()


def test_three_translations_always_create_unresolved_conflict_without_winner() -> None:
    result = CanonicalTerminologyReducer().reduce(
        project_id="project-1",
        variant_id="main",
        candidates=(
            _candidate("Dragon", "龙", "e-1"),
            _candidate("Dragon", "巨龙", "e-2"),
            _candidate("Dragon", "飞龙", "e-3"),
        ),
    )

    assert len(result.candidates) == 3
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.status is ConflictStatus.UNRESOLVED
    assert conflict.recommended_translation is None
    assert [item.normalized_translation for item in conflict.variants] == ["巨龙", "飞龙", "龙"]


def test_plugin_scope_keeps_same_pair_separate_but_conflict_group_is_project_variant_stable() -> None:
    result = CanonicalTerminologyReducer().reduce(
        project_id="project-1",
        variant_id="main",
        candidates=(
            _candidate("Dragon", "龙", "e-1", TermScope.project()),
            _candidate("Dragon", "巨龙", "e-2", TermScope.plugin("A.esm")),
        ),
    )

    assert len(result.candidates) == 2
    assert len(result.conflicts) == 1


def test_manual_translation_is_preserved_and_new_contradiction_requires_review() -> None:
    decision = TermDecision(
        "term-1",
        "project-1",
        "main",
        "Dragon",
        "dragon",
        "龙",
        status=DecisionStatus.MANUAL_CONFIRMED,
    )
    candidates = (
        _candidate("Dragon", "龙", "e-1"),
        _candidate("Dragon", "巨龙", "e-2"),
        _candidate("Sword", "剑", "e-3"),
    )

    result = ManualBaselineReconciler().reconcile(candidates, (decision,))

    assert result.preserved_decisions == (decision,)
    assert result.review_term_ids == ("term-1",)
    assert [item.original for item in result.effective_candidates] == ["Sword"]
    assert any(item.startswith("MANUAL_DECISION_NEW_CONFLICT") for item in result.diagnostics)


def test_suppression_prevents_automatic_candidate_from_becoming_effective() -> None:
    decision = TermDecision(
        "term-1",
        "project-1",
        "main",
        "Dragon",
        "dragon",
        "",
        status=DecisionStatus.ADOPTED,
        suppressed=True,
    )

    result = ManualBaselineReconciler().reconcile((_candidate("Dragon", "龙", "e-1"),), (decision,))

    assert result.effective_candidates == ()
    assert result.review_term_ids == ()
    assert result.diagnostics == ("MANUAL_SUPPRESSION_PRESERVED:term-1",)
