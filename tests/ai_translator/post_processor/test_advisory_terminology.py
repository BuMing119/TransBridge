from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.consistency_checker import ConsistencyChecker
from transbridge.ai_translator.post_processor.llm_arbiter import ArbitrationContext, LLMArbiter
from transbridge.ai_translator.post_processor.llm_refiner import LLMRefiner, RefineResult
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.converter.translation_entry import TranslationEntry


def _entry():
    return TranslationEntry("guard", "guard", "Bruma guards fighting predators", "布鲁玛卫兵对抗掠食者", 1, "QUST:FULL")


def _issue():
    entry = _entry()
    return PostProcessIssue(
        entry.id,
        "term_mismatch",
        "error",  # Legacy checkpoints can contain this old classification.
        "Guards differs from reference 瓜尔",
        entry.original,
        entry.translation,
        term="Guards",
        standard_translation="瓜尔",
    )


def _refined(status="term_suspect"):
    entry = _entry()
    return RefineResult(
        entry.id,
        entry.translation,
        entry.translation,
        confidence=0.95,
        review_status=status,
        review_reason="此处 guards 指卫兵，参考译法不适用",
    )


def test_reference_mismatch_is_collected_as_contextual_question():
    checker = ConsistencyChecker(SimpleNamespace(match_terms_for_entry=lambda _: {"Guards": "瓜尔"}))

    issue = checker.check(_entry())[0]

    assert issue.severity == "warning"
    assert "参考" in issue.message
    assert "标准译法" not in issue.message
    assert issue.term == "Guards"
    assert issue.standard_translation == "瓜尔"


@pytest.mark.parametrize("status", ["retained", "term_suspect", "needs_review"])
def test_semantic_review_does_not_force_incorrect_reference_or_repeat_arbitration(status):
    client = SimpleNamespace(chat=lambda **_: pytest.fail("completed semantic review must not be retried"))
    arbiter = LLMArbiter(client, strict_mode=True)

    decision = arbiter.arbitrate(ArbitrationContext(_entry(), [_issue()], _refined(status)))

    assert decision.verdict == ("pending" if status == "needs_review" else "pass")
    assert decision.review_status == status
    assert "不适用" in decision.review_reason


def test_unreviewed_terminology_only_finding_is_not_strict_rule_rejection():
    arbiter = LLMArbiter(SimpleNamespace(), strict_mode=True)

    assert arbiter._quick_decide(ArbitrationContext(_entry(), [_issue()])) is None
    assert arbiter._fallback_decision(ArbitrationContext(_entry(), [_issue()]), "unavailable").verdict == "pending"


def test_semantic_term_judgment_does_not_hide_broken_placeholder():
    arbiter = LLMArbiter(SimpleNamespace(), strict_mode=True)
    broken = replace(_issue(), issue_type="placeholder_missing", message="Missing {0}")

    entry = replace(_entry(), original=_entry().original + " {0}")
    decision = arbiter._quick_decide(ArbitrationContext(entry, [_issue(), broken], _refined()))

    assert decision.verdict == "reject"


@pytest.mark.parametrize("status", ["retained", "term_suspect", "needs_review"])
def test_legacy_processor_marks_review_completed_and_keeps_translation(status):
    entry = _entry()
    processor = PostProcessor(PostProcessorConfig(enable_quality_gate=False, enable_llm_arbitration=False))
    processor.register_checker(SimpleNamespace(check=lambda _: [_issue()]))
    processor._refiner = SimpleNamespace(refine_batch=lambda entries, issues: {entry.id: _refined(status)})

    result = processor.process_entries([entry])

    assert entry.translation == "布鲁玛卫兵对抗掠食者"
    assert entry.stage == 1
    assert result.processing_statuses[entry.id] == "completed"
    assert result.decisions[entry.id].review_status == status
    assert result.execution_result.rejected == 0


def test_refiner_receives_all_findings_and_returns_suspect_reference_without_changing_translation():
    entry = _entry()
    calls = []

    def chat(messages, **_):
        calls.append(messages)
        return json.dumps({
            "refined_translation": entry.translation,
            "fixes_applied": [],
            "confidence": 0.95,
            "review_status": "term_suspect",
            "review_reason": "guards 指卫兵",
        })

    refiner = LLMRefiner(SimpleNamespace(chat=chat))
    issues = [_issue(), replace(_issue(), issue_type="low_quality", message="Possible unnatural phrasing")]
    result = refiner.refine(entry, issues, terms={"Guards": "瓜尔"})

    assert result.valid and result.refined_translation == entry.translation
    assert result.review_status == "term_suspect"
    assert "advisory" in calls[0][0]["content"]
    assert "term_mismatch" in calls[0][1]["content"]
    assert "low_quality" in calls[0][1]["content"]
    assert "Reference translation: 瓜尔" in calls[0][1]["content"]
    assert result.review_reason == "guards 指卫兵"


def test_conflicting_retained_response_is_invalid_instead_of_silently_rewriting():
    refiner = LLMRefiner(SimpleNamespace())
    result = refiner._parse_refinement_response(
        _entry(), '{"refined_translation":"错误替换为瓜尔", "review_status":"retained"}'
    )
    assert not result.valid
    assert result.refined_translation == _entry().translation


def test_explicit_uncertainty_with_zero_confidence_is_completed_review():
    entry = _entry()
    processor = PostProcessor(PostProcessorConfig(enable_quality_gate=False, enable_llm_arbitration=False))
    processor.register_checker(SimpleNamespace(check=lambda _: [_issue()]))
    pending = replace(_refined("needs_review"), confidence=0.0, note="无法确定词义")
    processor._refiner = SimpleNamespace(refine_batch=lambda entries, issues: {entry.id: pending})

    result = processor.process_entries([entry])

    assert result.processing_statuses[entry.id] == "completed"
    assert result.decisions[entry.id].verdict == "pending"
    assert entry.translation == _entry().translation


def test_disabled_ai_review_keeps_reference_only_findings_pending_without_translation_failure():
    entry = _entry()
    processor = PostProcessor(
        PostProcessorConfig(
            enable_refinement=False,
            enable_quality_gate=False,
            enable_llm_arbitration=False,
        )
    )
    processor.register_checker(SimpleNamespace(check=lambda _: [_issue()]))

    result = processor.process_entries([entry])

    assert result.processing_statuses[entry.id] == "completed"
    assert result.decisions[entry.id].review_status == "needs_review"
    assert entry.translation == _entry().translation
