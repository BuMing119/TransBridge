from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue, PostProcessResult
from transbridge.ai_translator.post_processor.llm_arbiter import ArbitrationContext, LLMArbiter
from transbridge.ai_translator.post_processor.llm_refiner import LLMRefiner
from transbridge.ai_translator.post_processor.polisher import PolishResult
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.ai_translator.post_processor.review_decision import reviewed_decision
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, TranslationEntry


def entry():
    return TranslationEntry("guard", "guard", "Bruma guards fighting predators", "布鲁玛卫兵对抗掠食者", 1, "QUST:FULL")


def issue(value):
    return PostProcessIssue(
        value.id,
        "term_mismatch",
        "warning",
        "Guards 参考译法可能不适用",
        value.original,
        value.translation,
        term="Guards",
        standard_translation="瓜尔",
    )


def refine(value, *, batch=False, explicit=True):
    terms = {"Guards": "瓜尔"}
    lookups, requests = [], []

    def match(_entry):
        lookups.append(_entry)
        return terms

    def chat(messages, **_kwargs):
        requests.append(messages)
        assert "Guards" in messages[1]["content"] and "瓜尔" in messages[1]["content"]
        # Simulate another source publishing new references while this call is in flight.
        terms["Guards"] = "守卫"
        payload = {"entry_id": value.id, "refined_translation": value.translation, "confidence": 0.95}
        if explicit:
            payload.update(review_status="term_suspect", review_reason="guards 在此指卫兵，参考词义不适用")
        return json.dumps({"results": [payload]} if batch else payload)

    refiner = LLMRefiner(SimpleNamespace(chat=chat), term_manager=SimpleNamespace(match_terms_for_entry=match))
    result = (
        refiner.refine_batch([value], {value.id: [issue(value)]})[value.id]
        if batch
        else refiner.refine(value, [issue(value)])
    )
    assert len(lookups) == len(requests) == 1
    return result


def project(value, refined, *, decision=None, polished=None):
    result = PostProcessResult(
        issues=[issue(value)],
        refine_results={value.id: refined},
        decisions={value.id: decision} if decision else {},
        polish_results={value.id: polished} if polished else {},
        processing_statuses={value.id: "completed"},
    )
    return ProofreadPipeline(None, SimpleNamespace(enable_arbitration=False))._project((value,), result)[value.id]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_refiner_records_actual_request_snapshot_without_upgrading_legacy(batch, explicit):
    value = entry()
    result = refine(value, batch=batch, explicit=explicit)

    assert result.valid
    assert result.review_contract_version == int(explicit)
    assert result.review_reference_terms == {"Guards": "瓜尔"}
    assert result.review_before_text == result.reviewed_translation == value.translation
    assert result.detected_issues[0]["standard_translation"] == "瓜尔"
    assert result.review_status == ("term_suspect" if explicit else "needs_review")


def test_local_decision_inherits_real_refiner_evidence_and_project_closure_does_not_repeat_it():
    from transbridge.application.translation.task_scope import SourceTask
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
    from transbridge.ui.tools.ai_translator.task_term_closure import close_task_terms

    value = entry()
    refined = refine(value)
    decision = reviewed_decision(ArbitrationContext(value, [issue(value)], refined))
    result = project(value, refined, decision=decision)
    assert result.accepted
    assert result.review_contract_version == 1
    assert result.review_reference_terms == {"Guards": "瓜尔"}
    assert result.review_before_text == value.translation
    task = SourceTask("plugin", "plugin", "plugin.esp", TranslationEntryCollection([value]), (), (value,))
    outcome = SourceOutcome(task, polish={value.id: result})
    (closed,) = close_task_terms(
        (outcome,),
        resolve_terms=lambda *_: {"Guards": "瓜尔"},
        repair=lambda *_: pytest.fail("same reviewed text/reference must not trigger another model call"),
        stop_event=threading.Event(),
    )
    assert closed.polish[value.id] == result


@pytest.mark.parametrize("changed", [False, True])
def test_polish_only_retains_refiner_evidence_when_final_wording_is_unchanged(changed):
    value = entry()
    refined = refine(value)
    polished = PolishResult(
        value.id, value.translation, "布鲁玛守卫对抗掠食者" if changed else value.translation, confidence=0.95
    )
    context = ArbitrationContext(value, [issue(value)], refined, polished)
    local = reviewed_decision(context)
    assert (local is None) is changed
    result = project(value, refined, decision=local, polished=polished)
    assert result.review_contract_version == (0 if changed else 1)
    assert result.review_status == ("needs_review" if changed else "term_suspect")
    if changed:
        assert result.target_stage == STAGE_QUESTIONABLE
        assert "改变" in result.review_reason


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_real_arbitration_captures_its_final_candidate_and_supplied_references(batch, explicit):
    value = entry()
    refined = refine(value)
    final = "布鲁玛守卫对抗掠食者"
    polished = PolishResult(value.id, value.translation, final, confidence=0.95)
    context = ArbitrationContext(value, [issue(value)], refined, polished)
    requests = []

    def chat(messages, **_kwargs):
        requests.append(messages)
        content = messages[1]["content"]
        sent, _end = json.JSONDecoder().raw_decode(content[content.index('{"reference_terms":') :])
        assert final in content
        assert content.count('{"reference_terms":') == 1
        assert sent["reference_terms"] == {"Guards": "瓜尔"}
        assert sent["detected_issues"][0]["standard_translation"] == "瓜尔"
        payload = {"entry_id": value.id, "verdict": "pass", "confidence": 0.95, "reason": "语境合适"}
        if explicit:
            payload.update(review_status="term_suspect", review_reason="参考瓜尔在此仍不适用")
        return json.dumps({"results": [payload]} if batch else payload)

    arbiter = LLMArbiter(SimpleNamespace(chat=chat))
    decision = arbiter.arbitrate_batch([context])[value.id] if batch else arbiter.arbitrate(context)
    result = project(value, refined, decision=decision, polished=polished)
    assert len(requests) == 1
    assert result.accepted
    assert result.review_contract_version == int(explicit)
    assert result.review_before_text == final
    assert result.review_reference_terms == {"Guards": "瓜尔"}
    assert result.review_status == ("term_suspect" if explicit else "needs_review")


def test_old_or_incomplete_evidence_is_not_upgraded_by_local_decision_or_projection():
    value = entry()
    refined = replace(refine(value), review_contract_version=0, reviewed_translation=None)
    local = reviewed_decision(ArbitrationContext(value, [issue(value)], refined))
    assert local.review_contract_version == 0
    result = project(value, refined, decision=local)
    assert result.review_contract_version == 0
    assert result.review_status == "needs_review"


def test_failed_later_arbitration_cannot_reuse_earlier_refiner_approval():
    value = entry()
    refined = refine(value)
    arbiter = LLMArbiter(SimpleNamespace())
    failed = arbiter._parse_arbitration_response(value.id, "broken response")
    result = project(value, refined, decision=failed)
    assert result.review_contract_version == 0
    assert result.review_status == "needs_review"


@pytest.mark.parametrize("stage", ["refine", "arbitrate"])
def test_explicit_revised_judgment_with_unchanged_reviewed_text_is_recorded_as_retained(stage):
    value = entry()

    def chat(**_kwargs):
        return json.dumps({
            "refined_translation": value.translation,
            "verdict": "pass",
            "confidence": 0.95,
            "review_status": "revised",
            "review_reason": "当前译文合适",
        })

    if stage == "refine":
        refined = LLMRefiner(SimpleNamespace(chat=chat)).refine(value, [issue(value)], terms={"Guards": "瓜尔"})
        result = reviewed_decision(ArbitrationContext(value, [issue(value)], refined))
    else:
        result = LLMArbiter(SimpleNamespace(chat=chat)).arbitrate(ArbitrationContext(value, [issue(value)]))
    assert result.review_status == "retained"
    assert result.review_contract_version == 1
    assert result.review_before_text == result.reviewed_translation == value.translation
