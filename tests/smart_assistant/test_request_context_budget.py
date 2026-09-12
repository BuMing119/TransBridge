from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from transbridge.application.assistant_requests.models import Evidence, RequestItem, RequestRevision, UserRequest
from transbridge.infra.llm_tool_calling import LlmToolDefinition, LlmToolProtocolError
from transbridge.smart_assistant.context_budget import ContextBudget, ContextBudgetExceeded
from transbridge.smart_assistant.request_context_assembler import RequestContextAssembler, assign_history_requests


def _message(mid, role, content, **kwargs):
    return {"message_id": mid, "role": role, "content": content, **kwargs}


def _budget(window=4000):
    return ContextBudget(window, 100, 50, estimator=len, estimator_label="test-character-units")


@pytest.mark.parametrize("window,expects_summary", [(1000, False), (12000, True)])
def test_optional_summary_respects_budget_and_keeps_current_input(window, expects_summary):
    from transbridge.application.assistant_requests.summaries import plan_summary

    request = UserRequest("r", "s", "goal", (RequestItem("i", "answer"),), source_message_ids=("current",))
    history = [_message(f"a{n}", "assistant", f"Decision {n}: " + "details " * 50, request_id="r") for n in range(30)]
    history.append(_message("current", "user", "Current input", request_id="r"))
    summary = plan_summary(request, history)
    projection = RequestContextAssembler(_budget(window)).assemble(
        history, request_state={"request_id": "r", "constraints": ["Keep variable names"]}, summary=summary
    )
    has_summary = any('"kind":"request_history_summary"' in m.get("content", "") for m in projection.messages)
    assert has_summary is expects_summary
    assert projection.usage.fits
    assert "current" in projection.selected_message_ids
    assert any("Keep variable names" in m.get("content", "") for m in projection.messages)
    if expects_summary:
        assert not set(summary.source_ids) & set(projection.selected_message_ids)


def test_request_sources_amendments_and_answers_are_scoped_without_mutating_history():
    item = RequestItem("i", "explain")
    first = UserRequest("a", "s", "first", (item,), source_message_ids=("shared",))
    second = replace(
        first,
        request_id="b",
        revisions=(RequestRevision(1, "old", (), (item,), "amend-b"),),
        evidence=(Evidence("e", "answer", 1, ("i",), "answer-b"),),
    )
    history = [
        _message("shared", "user", "Explain A and B"),
        _message("amend-b", "user", "B needs detail"),
        _message("answer-b", "assistant", "B detail"),
    ]
    before = deepcopy(history)
    scoped = assign_history_requests(history, (first, second), {"shared": "a"})
    assert history == before
    for request_id in ("a", "b"):
        result = RequestContextAssembler(_budget()).assemble(scoped, request_state={"request_id": request_id})
        assert result.selected_message_ids == (("shared",) if request_id == "a" else ("shared", "amend-b", "answer-b"))


def test_batch_routing_control_and_its_result_are_excluded_from_execution_context():
    history = [
        _message("u", "user", "work"),
        _message("route", "assistant", "", tool_calls=[{"id": "c", "name": "submit_request_routing", "arguments": {}}]),
        _message("result", "tool", "batch receipt", tool_call_id="c", name="submit_request_routing"),
    ]
    scoped = assign_history_requests(history, (), {"u": "a", "route": "a"})
    result = RequestContextAssembler(_budget()).assemble(scoped, request_state={"request_id": "a"})
    assert result.selected_message_ids == ("u",)
    assert result.omitted_message_ids == ("route", "result")


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


def test_other_request_last_input_does_not_become_required_current_input():
    history = [
        _message("a", "user", "A original question", request_id="A"),
        _message("b", "user", "B unrelated question", request_id="B"),
    ]
    projection = RequestContextAssembler(_budget()).assemble(history, request_state={"request_id": "A"})
    assert projection.selected_message_ids == ("a",)


def test_other_request_last_tool_group_cannot_bypass_context_scope():
    history = [
        _message("input-a", "user", "Continue A", request_id="A"),
        _message("call-b", "assistant", "", request_id="B", tool_calls=[{"id": "b", "name": "read", "arguments": {}}]),
        _message("result-b", "tool", "B evidence", request_id="B", tool_call_id="b"),
    ]
    projection = RequestContextAssembler(_budget()).assemble(
        history, request_state={"request_id": "A"}, continuation={"request_id": "A"}
    )
    assert projection.selected_message_ids == ("input-a",)
    assert "B evidence" not in json.dumps(projection.messages)


def test_explicit_required_foreign_evidence_is_rejected():
    history = [_message("b", "user", "B input", request_id="B")]
    with pytest.raises(ValueError, match="scope"):
        RequestContextAssembler(_budget()).assemble(
            history, request_state={"request_id": "A"}, required_message_ids=["b"]
        )


def test_required_medium_result_can_shrink_below_initial_preview_threshold():
    history = [
        _message("u", "user", "read"),
        _message("a", "assistant", "", tool_calls=[{"id": "call", "name": "read", "arguments": {}}]),
        _message("r", "tool", "x" * 750, tool_call_id="call"),
    ]
    projection = RequestContextAssembler(_budget(850)).assemble(history)
    assert projection.usage.fits
    assert projection.result_references[0]["total_characters"] == 750
    assert projection.result_references[0]["range"][1] < 750
