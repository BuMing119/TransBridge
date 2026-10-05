from __future__ import annotations

import json

import pytest

from tests.application.translation.test_proofread_stage import _candidate, _PreparedClient, _result
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
from transbridge.application.translation.proofread_response import apply_proofread_response
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.infra.llm_structured_outputs import LlmStructuredOutputInvalidResponseError


def test_duplicate_unknown_and_invalid_identity_do_not_poison_unrelated_results():
    candidates = tuple(_candidate(key) for key in ("valid", "duplicate", "missing", "1", "extra"))
    results = [
        _result(candidates[0], "updated"),
        _result(candidates[1], "first"),
        {**_result(candidates[1], "second"), "reason": "duplicate is invalid too"},
        _result(_candidate("unknown"), "unrequested"),
        {
            "entry_key": {"namespace": candidates[3].entry_key.to_dict()["namespace"], "local_key": 1},
            "final_translation": "bad",
        },
        {"entry_key": {**candidates[4].entry_key.to_dict(), "alias": "x"}, "final_translation": "bad"},
    ]
    outcome = apply_proofread_response(candidates, json.dumps({"results": results}), phase="proofread")
    assert [item.accepted for item in outcome.candidates] == [True, False, False, False, False]
    assert outcome.candidates[0].text == "updated"
    assert {item.code for item in outcome.diagnostics} == {
        "PROOFREAD_RESULT_ITEM_MALFORMED",
        "PROOFREAD_RESPONSE_UNKNOWN_KEY",
        "PROOFREAD_RESPONSE_DUPLICATE_KEY",
        "PROOFREAD_RESPONSE_MISSING_KEY",
        "PROOFREAD_RESPONSE_SCHEMA_INVALID",
    }


@pytest.mark.parametrize(
    "response",
    [
        "{",
        "[]",
        '{"results":{}}',
        '{"results":[],"results":[]}',
        '{"results":[{"entry_key":{"namespace":"n","local_key":"one","local_key":"two"}}]}',
    ],
)
def test_unreliable_envelope_is_whole_batch_failure(response):
    candidates = (_candidate("one"), _candidate("two"))
    outcome = apply_proofread_response(candidates, response, phase="proofread")
    assert outcome.structurally_malformed
    assert not any(item.accepted for item in outcome.candidates)


def test_root_extra_fields_preserve_valid_items_and_remain_visible_after_recovery():
    good, missing = _candidate("good"), _candidate("missing")
    calls = 0

    def respond(_messages):
        nonlocal calls
        calls += 1
        payload = (
            {"results": [_result(good, "updated")], "explanation": "private text"}
            if calls == 1
            else {"results": [_result(missing, "recovered")]}
        )
        raise LlmStructuredOutputInvalidResponseError("invalid", raw_response=json.dumps(payload))

    outcome = ProofreadStage(_PreparedClient(respond))((good, missing))
    assert [item.text for item in outcome.candidates] == ["updated", "recovered"]
    assert all(item.accepted for item in outcome.candidates)
    root_diagnostic = next(item for item in outcome.diagnostics if item.code == "PROOFREAD_RESPONSE_EXTRA_FIELDS")
    assert dict(root_diagnostic.details)["unexpected_fields"] == ["explanation"]
    assert "private text" not in repr(outcome.diagnostics)


def test_cancelled_subset_recovery_keeps_first_pass_evidence_without_further_requests():
    good, missing = _candidate("good"), _candidate("missing")
    calls = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        calls.append([item["entry_key"]["local_key"] for item in entries])
        if len(calls) == 1:
            raise LlmStructuredOutputInvalidResponseError(
                "invalid",
                raw_response=json.dumps({
                    "results": [_result(good, "updated"), {"entry_key": missing.entry_key.to_dict()}]
                }),
            )
        raise AiRequestCancelledError("cancelled")

    outcome = ProofreadStage(_PreparedClient(respond))((good, missing))
    assert calls == [["good", "missing"], ["missing"]]
    assert outcome.candidates[0].accepted and outcome.candidates[0].text == "updated"
    assert not outcome.candidates[1].accepted
    assert any(item.code == "PROOFREAD_LLM_CALL_CANCELLED" for item in outcome.diagnostics)


def test_live_recovery_events_do_not_wait_for_entire_stage_completion():
    candidate = _candidate("one")
    events = []
    calls = 0

    def respond(_messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            return '{"results":[]}'
        assert events == ["正在重试 1 条未完成条目"]
        return json.dumps({"results": [_result(candidate, "updated")]})

    outcome = ProofreadStage(_PreparedClient(respond)).run((candidate,), event_callback=events.append)
    assert outcome.candidates[0].accepted
    assert events[:2] == ["正在重试 1 条未完成条目", "已恢复 1 条"]
