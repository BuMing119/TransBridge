from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from transbridge.infra.llm_tool_calling import LlmToolDefinition, LlmToolProtocolError
from transbridge.smart_assistant.context_budget import ContextBudget, ContextBudgetExceeded
from transbridge.smart_assistant.request_context_assembler import RequestContextAssembler


def _message(mid, role, content, **kwargs):
    return {"message_id": mid, "role": role, "content": content, **kwargs}


def _budget(window=4000):
    return ContextBudget(window, 100, 50, estimator=len, estimator_label="test-character-units")


def test_21st_turn_keeps_required_constraints_and_chronological_deduplicated_input():
    history = [_message("system", "system", "Rules")]
    for i in range(21):
        history.extend([_message(f"u{i}", "user", f"question {i}"), _message(f"a{i}", "assistant", "answer" * 50)])
    history.append(_message("current", "user", "Current question"))
    history.append(deepcopy(history[-1]))
    state = {"goal": "translate", "constraints": ["Never translate variable names"], "pending_items": ["summary"]}
    projection = RequestContextAssembler(_budget(2000)).assemble(
        history, current_input_id="current", request_state=state
    )
    assert projection.usage.fits
    assert "Never translate variable names" in projection.messages[1]["content"]
    assert projection.messages[-1]["content"] == "Current question"
    assert projection.selected_message_ids.count("current") == 1
    assert projection.omitted_message_ids
    order = [record["message_id"] for record in history[:-1] if record["message_id"] in projection.selected_message_ids]
    assert order == list(projection.selected_message_ids)


def test_schemas_output_and_protocol_reserve_are_jointly_budgeted():
    history = [_message("current", "user", "question")]
    tools = [LlmToolDefinition("lookup", "description" * 100, {"type": "object"})]
    budget = _budget(600)
    assembler = RequestContextAssembler(budget)
    assert assembler.assemble(history).usage.total <= 600
    with pytest.raises(ContextBudgetExceeded) as error:
        assembler.assemble(history, tools=tools)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert error.value.usage.tool_schemas > 1000
    assert error.value.usage.output_reserve == 100
    assert error.value.usage.protocol_margin == 50


def test_oversized_required_input_and_constraints_fail_without_silent_removal():
    assembler = RequestContextAssembler(_budget(500))
    with pytest.raises(ContextBudgetExceeded):
        assembler.assemble([_message("u", "user", "a" * 1000)])
    with pytest.raises(ContextBudgetExceeded):
        assembler.assemble([_message("u", "user", "hi")], request_state={"constraints": ["a" * 1000]})


def test_tool_groups_kept_or_omitted_whole_and_full_result_reference_matches_original():
    original_result = json.dumps({"evidence": "原始数据" * 5000}, ensure_ascii=False)
    history = [
        _message("u", "user", "translate"),
        _message("a", "assistant", "", tool_calls=[{"id": "call", "name": "translate", "arguments": {}}]),
        _message("r", "tool", original_result, tool_call_id="call", name="translate"),
        _message("current", "user", "Explain next steps"),
    ]
    before = deepcopy(history)
    projection = RequestContextAssembler(_budget(1200), result_preview_chars=50).assemble(
        history, required_message_ids=["r"]
    )
    assert "a" in projection.selected_message_ids and "r" in projection.selected_message_ids
    result = next(m for m in projection.messages if m["role"] == "tool")
    payload = json.loads(result["content"])
    assert payload["reference"]["sha256"] == hashlib.sha256(original_result.encode()).hexdigest()
    assert payload["reference"]["message_id"] == "r"
    assert payload["preview"] == original_result[:50]
    assert payload["material_only"] is True
    assert history == before
    omitted = RequestContextAssembler(_budget(300), result_preview_chars=50).assemble(history)
    assert "a" not in omitted.selected_message_ids and "r" not in omitted.selected_message_ids


@pytest.mark.parametrize(
    "history",
    [
        [_message("r", "tool", "{}", tool_call_id="call")],
        [_message("a", "assistant", "", tool_calls=[{"id": "call", "name": "x", "arguments": {}}])],
        [
            _message("a", "assistant", "", tool_calls=[{"id": "call", "name": "x", "arguments": {}}]),
            _message("u", "assistant", "duplicate", tool_calls=[{"id": "call", "name": "x", "arguments": {}}]),
            _message("r", "tool", "{}", tool_call_id="call"),
        ],
    ],
)
def test_invalid_tool_protocol_is_not_sent_to_provider(history):
    with pytest.raises(LlmToolProtocolError):
        RequestContextAssembler(_budget()).assemble(history)


def test_continuation_is_separate_event_and_never_reappends_user_input():
    history = [_message("u", "user", "translate"), _message("a", "assistant", "started")]
    projection = RequestContextAssembler(_budget()).assemble(
        history, continuation={"event_id": "event1", "reason": "translation completed"}
    )
    assert sum(m["role"] == "user" for m in projection.messages) == 1
    assert "continuation_event" in projection.messages[0]["content"]
    assert history == [_message("u", "user", "translate"), _message("a", "assistant", "started")]


def test_conflicting_message_ids_and_missing_required_evidence_are_rejected():
    assembler = RequestContextAssembler(_budget())
    with pytest.raises(ValueError, match="Conflicting content"):
        assembler.assemble([_message("u", "user", "one"), _message("u", "user", "two")])
    with pytest.raises(ValueError, match="do not exist"):
        assembler.assemble([], required_message_ids=["missing"])


def test_default_budget_identifies_conservative_estimate_without_loading_model():
    budget = ContextBudget()
    usage = budget.measure([{"role": "user", "content": "中文"}])
    assert usage.estimator_label == "utf8-bytes-v1-conservative"
    assert usage.messages >= len(json.dumps([{"role": "user", "content": "中文"}], ensure_ascii=False).encode())


def test_required_large_tool_preview_can_shrink_while_keeping_reference_and_protocol():
    history = [
        _message("u", "user", "run"),
        _message("a", "assistant", "", tool_calls=[{"id": "call", "name": "x", "arguments": {}}]),
        _message("r", "tool", "x" * 10000, tool_call_id="call", name="x"),
    ]
    projection = RequestContextAssembler(_budget(900)).assemble(history)
    reference = projection.result_references[0]
    assert reference["range"][1] < 800
    assert reference["total_characters"] == 10000
    assert projection.selected_message_ids == ("u", "a", "r")
    assert projection.usage.fits


def test_other_requests_material_is_excluded_but_selected_request_constraints_are_pinned():
    history = [
        _message("other", "user", "private unrelated result", request_id="B"),
        _message("current", "user", "current", request_id="A"),
    ]
    projection = RequestContextAssembler(_budget()).assemble(history, request_state={"request_id": "A"})
    assert projection.selected_message_ids == ("current",)
    assert projection.omitted_message_ids == ("other",)


def test_interleaved_late_result_is_inert_material_without_reordering_evidence():
    history = [
        _message("call", "assistant", "read", tool_calls=[{"id": "c", "name": "read", "arguments": {}}]),
        _message("input", "user", "new question"),
        _message("result", "tool", "old result", tool_call_id="c"),
    ]
    original = deepcopy(history)
    projection = RequestContextAssembler(_budget()).assemble(history)
    assert projection.selected_message_ids == ("call", "input", "result")
    assert [m["role"] for m in projection.messages] == ["assistant", "user", "assistant"]
    assert "historical_tool_result" in projection.messages[-1]["content"]
    assert history == original
