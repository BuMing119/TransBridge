from __future__ import annotations

import json

import pytest

from tests.application.translation.test_proofread_stage import _candidate, _PreparedClient
from transbridge.ai_translator.structured_schemas import PROOFREAD_OUTPUT_SCHEMA
from transbridge.application.contracts import ErrorCategory
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.infra.llm_structured_outputs import validate_structured_output


def _response(entries, *, invalid=False, all_invalid=False):
    results = [{"entry_key": entry["entry_key"], "final_translation": "updated"} for entry in entries]
    if invalid:
        results[-1]["reason"] = "unexpected explanation"
    if all_invalid:
        for item in results:
            item["reason"] = "unexpected explanation"
    return validate_structured_output(json.dumps({"results": results}), PROOFREAD_OUTPUT_SCHEMA)


@pytest.mark.parametrize("count", [2, 6])
@pytest.mark.parametrize("persistent", [False, True])
def test_schema_errors_retry_only_invalid_entries_and_keep_valid_results(count, persistent):
    candidates = tuple(_candidate(str(index)) for index in range(count))
    calls = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        calls.append([item["entry_key"]["local_key"] for item in entries])
        return _response(entries, invalid=len(calls) == 1 or persistent)

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)(candidates)
    keys = [str(index) for index in range(count)]
    assert calls == [keys, keys[-1:]]
    assert [item.accepted for item in outcome.candidates] == [True] * (count - 1) + [not persistent]
    errors = [item for item in outcome.diagnostics if item.code == "PROOFREAD_RESPONSE_SCHEMA_INVALID"]
    if persistent:
        assert len(errors) == 1
        details = dict(errors[0].details)
        assert details["entry_key"] == candidates[-1].entry_key.to_dict()
        assert details["validation_details"]["unexpected_fields"] == ["reason"]
        assert outcome.candidates[-1].text == candidates[-1].before_text
        assert "unexpected explanation" not in repr(outcome.diagnostics)
    else:
        assert not errors


def test_single_schema_failure_has_two_attempts_and_never_counts_as_questionable_success():
    candidate = _candidate("one", original="<Hiccup>", text="（打嗝）")
    client = _PreparedClient(lambda messages: _response(json.loads(messages[1]["content"])["entries"], invalid=True))
    outcome = ProofreadStage(client)((candidate,))
    assert len(client.messages) == 2
    assert not outcome.candidates[0].accepted
    assert outcome.candidates[0].text == candidate.before_text
    assert outcome.diagnostics[0].code == "PROOFREAD_RESPONSE_SCHEMA_INVALID"


def test_cancel_during_schema_split_does_not_start_second_half():
    candidates = tuple(_candidate(str(index)) for index in range(6))
    calls = 0

    def respond(messages):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise AiRequestCancelledError("cancelled")
        return _response(json.loads(messages[1]["content"])["entries"], all_invalid=True)

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)(candidates)
    assert calls == 3
    assert not any(item.accepted for item in outcome.candidates)
    assert any(item.category is ErrorCategory.CANCELLED for item in outcome.diagnostics)


def test_schema_recovery_does_not_discard_success_from_either_attempt():
    candidates = tuple(_candidate(str(index)) for index in range(5))
    calls = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        calls.append([item["entry_key"]["local_key"] for item in entries])
        if len(calls) == 1:
            return _response(entries[:1])
        return _response(entries, invalid=len(calls) == 2)

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)(candidates)
    assert calls == [["0", "1", "2", "3", "4"], ["1", "2", "3", "4"]]
    assert all(item.accepted and item.text == "updated" for item in outcome.candidates[:-1])
    assert not outcome.candidates[-1].accepted
    assert {item.code for item in outcome.diagnostics} == {
        "PROOFREAD_RECOVERY_SUCCEEDED",
        "PROOFREAD_RESPONSE_SCHEMA_INVALID",
    }


def test_replay_mixed_four_item_response_keeps_three_and_reports_all_problems_of_fourth():
    """Synthetic version of a model copying input fields instead of producing output."""
    candidates = tuple(_candidate(str(index)) for index in range(4))
    calls = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        calls.append([item["entry_key"]["local_key"] for item in entries])
        results = [
            {"entry_key": entry["entry_key"], "final_translation": f"correct-{entry['entry_key']['local_key']}"}
            if entry["entry_key"]["local_key"] != "3"
            else entry
            for entry in entries
        ]
        return validate_structured_output(json.dumps({"results": results}), PROOFREAD_OUTPUT_SCHEMA)

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)(candidates)
    assert calls == [["0", "1", "2", "3"], ["3"]]
    assert [item.accepted for item in outcome.candidates] == [True, True, True, False]
    assert [item.text for item in outcome.candidates] == ["correct-0", "correct-1", "correct-2", "Current"]
    diagnostic = outcome.diagnostics[0]
    assert diagnostic.message == "未返回校对译文"
    validation = dict(diagnostic.details)["validation_details"]
    assert validation["missing_fields"] == ["final_translation"]
    assert {error["validator"] for error in validation["errors"]} == {"required", "additionalProperties"}


def test_split_only_remaining_invalid_subset_after_partial_recovery():
    candidates = tuple(_candidate(str(index)) for index in range(6))
    calls = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        calls.append([item["entry_key"]["local_key"] for item in entries])
        if len(calls) == 1:
            return _response(entries, all_invalid=True)
        results = [{"entry_key": item["entry_key"], "final_translation": "updated"} for item in entries]
        if len(calls) == 2:
            for item in results[-2:]:
                item["reason"] = "unexpected explanation"
        return validate_structured_output(json.dumps({"results": results}), PROOFREAD_OUTPUT_SCHEMA)

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)(candidates)
    assert calls == [["0", "1", "2", "3", "4", "5"], ["0", "1", "2", "3", "4", "5"], ["4"], ["5"]]
    assert all(item.accepted and item.text == "updated" for item in outcome.candidates)
    recovered = next(item for item in outcome.diagnostics if item.code == "PROOFREAD_RECOVERY_SUCCEEDED")
    assert dict(recovered.details) == {"recovered_count": 6, "final_failed_count": 0}
