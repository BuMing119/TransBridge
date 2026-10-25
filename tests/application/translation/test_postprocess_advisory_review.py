from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.translation_entry_outcomes import _post_process_failures
from transbridge.application.contracts import DiagnosticSeverity, OperationOutcome
from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot
from transbridge.application.translation.postprocess_review import annotate_review
from transbridge.application.translation.postprocess_stages import (
    CheckerStage,
    LlmClientPostProcessPort,
    LlmPostProcessStage,
    PostProcessLlmPhase,
)
from transbridge.converter.translation_entry import TranslationEntry


def _candidate():
    entry = TranslationEntry(
        "guard", "guard", "Bruma guards fighting predators", "布鲁玛卫兵对抗掠食者", 1, "QUST:FULL"
    )
    return PostProcessCandidate(
        "run",
        entry.identity,
        entry.revision,
        entry.original,
        entry.translation,
        entry.translation,
        1,
        context=entry.context,
    )


def test_checker_evidence_context_and_terms_reach_one_semantic_request_and_report():
    candidate = _candidate()
    issues = [
        PostProcessIssue("guard", "term_mismatch", "warning", "Reference differs", candidate.original, candidate.text),
        PostProcessIssue("guard", "low_quality", "warning", "Check phrasing", candidate.original, candidate.text),
    ]
    checked = CheckerStage("detect", SimpleNamespace(check=lambda _: issues))((candidate,))
    captured = []

    def chat(messages, **_):
        captured.append(messages)
        return json.dumps({
            "results": [
                {
                    "entry_key": candidate.entry_key.to_dict(),
                    "value": candidate.text,
                    "review_status": "term_suspect",
                    "review_reason": "guards 指卫兵，参考不适用",
                }
            ]
        })

    result = LlmPostProcessStage(
        PostProcessLlmPhase.REFINE,
        LlmClientPostProcessPort(SimpleNamespace(chat=chat)),
        term_resolver=lambda _: {"Guards": "瓜尔"},
    )(checked.candidates)

    sent = json.loads(captured[0][1]["content"])["entries"][0]
    assert sent["context"] == "QUST:FULL"
    assert sent["reference_terms"] == {"Guards": "瓜尔"}
    assert {issue["issue_type"] for issue in sent["detected_issues"]} == {"term_mismatch", "low_quality"}
    assert "advisory" in captured[0][0]["content"]
    reviewed = result.candidates[0]
    assert reviewed.text == candidate.text and reviewed.accepted
    assert dict(reviewed.report_details)["review_status"] == "term_suspect"
    assert dict(reviewed.report_details)["review_contract_version"] == 1
    assert dict(reviewed.report_details)["review_reference_terms"] == {"Guards": "瓜尔"}
    assert len(dict(reviewed.report_details)["detected_issues"]) == 2


def test_pending_arbitration_keeps_translation_available_for_review():
    candidate = _candidate()

    def chat(messages, **_):
        return json.dumps({
            "results": [
                {
                    "entry_key": candidate.entry_key.to_dict(),
                    "value": "pending",
                    "review_status": "needs_review",
                    "review_reason": "More context needed",
                }
            ]
        })

    result = LlmPostProcessStage(PostProcessLlmPhase.ARBITRATE, LlmClientPostProcessPort(SimpleNamespace(chat=chat)))((
        candidate,
    ))

    reviewed = result.candidates[0]
    assert reviewed.accepted
    assert reviewed.text == candidate.text
    assert not result.failed
    assert dict(reviewed.report_details)["review_status"] == "needs_review"


def test_uncertain_refinement_preserves_current_text_and_records_suggestion():
    candidate = _candidate()

    def chat(messages, **_):
        return json.dumps({
            "results": [
                {
                    "entry_key": candidate.entry_key.to_dict(),
                    "value": "不确定的改写",
                    "review_status": "needs_review",
                    "review_reason": "More context needed",
                }
            ]
        })

    result = LlmPostProcessStage(PostProcessLlmPhase.REFINE, LlmClientPostProcessPort(SimpleNamespace(chat=chat)))((
        candidate,
    ))

    reviewed = result.candidates[0]
    assert reviewed.text == candidate.text
    assert dict(reviewed.report_details)["proposed_translation"] == "不确定的改写"


@pytest.mark.parametrize("changed", [True, False])
def test_explicit_review_status_describes_actual_text_change(changed):
    before = _candidate()
    text = "修改译文" if changed else before.text
    after = before.with_text(text, "refine")
    result = annotate_review(
        before,
        after,
        {"review_status": "retained" if changed else "revised", "review_reason": "语境判断"},
        "refine",
        text,
    )
    assert dict(result.report_details)["review_status"] == ("revised" if changed else "retained")


def test_missing_semantic_metadata_is_pending_without_discarding_usable_text():
    before = _candidate()
    after = before.with_text("可用候选", "refine")

    result = annotate_review(before, after, {}, "refine", after.text)

    assert result.text == "可用候选" and result.accepted
    assert dict(result.report_details)["review_status"] == "needs_review"
    assert dict(result.report_details)["review_contract_version"] == 0


@pytest.mark.parametrize("change", ["none", "text", "reference"])
def test_prior_evidence_only_survives_unchanged_candidate_and_references(change):
    before = _candidate().with_report_details({
        "review_status": "term_suspect",
        "review_reason": "参考译法不适用",
        "review_contract_version": 1,
        "review_reference_terms": {"Guards": "瓜尔"},
        "reference_terms": {"Guards": "瓜尔"},
        "detected_issues": [],
    })
    after = before.with_text("新译文" if change == "text" else before.text, "polish")
    if change == "reference":
        after = after.with_report_details({**dict(after.report_details), "reference_terms": {"Guards": "卫士"}})

    result = annotate_review(before, after, {}, "polish", after.text)

    assert dict(result.report_details)["review_status"] == ("term_suspect" if change == "none" else "needs_review")
    assert dict(result.report_details)["review_contract_version"] == (1 if change == "none" else 0)


def test_later_retained_review_compares_against_its_actual_input():
    original = _candidate()
    refined = annotate_review(
        original,
        original.with_text("新译文", "refine"),
        {"review_status": "revised", "review_reason": "修正表达"},
        "refine",
        "新译文",
    )
    polished = annotate_review(
        refined,
        refined.with_text("新译文", "polish"),
        {"review_status": "retained", "review_reason": "保持自然表达"},
        "polish",
        "新译文",
    )
    assert dict(polished.report_details)["review_before_text"] == "新译文"


@pytest.mark.parametrize("execution_failed", [False, True])
def test_detected_error_does_not_fail_translation_after_semantic_review_but_execution_failure_does(execution_failed):
    candidate = _candidate()
    issue = PostProcessIssue(
        "guard",
        "low_quality",
        "error",
        "Possible incorrect rendering",
        candidate.original,
        candidate.text,
        execution_failed=execution_failed,
    )
    checked = CheckerStage("detect", SimpleNamespace(check=lambda _: [issue]))((candidate,))

    def chat(messages, **_):
        return json.dumps({
            "results": [
                {
                    "entry_key": candidate.entry_key.to_dict(),
                    "value": "布鲁玛卫兵正在对抗掠食者",
                    "review_status": "revised",
                    "review_reason": "已结合语境修正表达",
                }
            ]
        })

    reviewed = LlmPostProcessStage(PostProcessLlmPhase.REFINE, LlmClientPostProcessPort(SimpleNamespace(chat=chat)))(
        checked.candidates
    )
    snapshot = ReportSnapshot(
        "test",
        "run",
        OperationOutcome.COMPLETED,
        1,
        1,
        reviewed.candidates,
        (checked, reviewed),
        (*checked.diagnostics, *reviewed.diagnostics),
    )
    commit = SimpleNamespace(value={"applied_keys": [candidate.entry_key.to_dict()]}, diagnostics=())
    failures = _post_process_failures({candidate.entry_key}, ("detect", "refine"), snapshot, commit)

    assert bool(failures) is execution_failed
    assert checked.diagnostics[0].severity == (
        DiagnosticSeverity.ERROR if execution_failed else DiagnosticSeverity.WARNING
    )


def test_postprocess_still_rejects_model_output_that_damages_protected_syntax():
    from dataclasses import replace

    candidate = replace(_candidate(), original="Guards <Alias=Hero>", text="卫兵 <Alias=Hero>")

    def chat(messages, **_):
        return json.dumps({
            "results": [
                {
                    "entry_key": candidate.entry_key.to_dict(),
                    "value": "卫兵",
                    "review_status": "needs_review",
                    "review_reason": "不确定",
                }
            ]
        })

    result = LlmPostProcessStage(PostProcessLlmPhase.REFINE, LlmClientPostProcessPort(SimpleNamespace(chat=chat)))((
        candidate,
    ))

    assert not result.candidates[0].accepted
    assert result.candidates[0].text == "卫兵 <Alias=Hero>"
    assert result.diagnostics[0].code == "POSTPROCESS_PROTECTED_SYNTAX_MISMATCH"
    assert result.diagnostics[0].severity is DiagnosticSeverity.ERROR
