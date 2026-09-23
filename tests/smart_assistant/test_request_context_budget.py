"""Current context projection, protocol normalization and joint provider budget checks."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from transbridge.application.assistant_context.projection import append_context, authorized_records, project_result
from transbridge.application.assistant_context.protocol import normalize_records, protocol_groups
from transbridge.application.assistant_requests.history_scope import assign_history_requests
from transbridge.application.assistant_requests.models import (
    Evidence,
    RequestItem,
    RequestRevision,
    UserRequest,
    digest,
)
from transbridge.infra.llm_tool_calling import LlmToolDefinition, LlmToolProtocolError
from transbridge.smart_assistant.context_budget import ContextBudget, ContextBudgetExceeded


def _message(mid, role, content, **kwargs):
    return {"message_id": mid, "role": role, "content": content, **kwargs}


def _request(identity="a"):
    return UserRequest(identity, "s", "goal", (RequestItem("i", "explain"),))


def _budget(window=4000):
    return ContextBudget(window, 100, 50, estimator=len, estimator_label="test-character-units")


def test_request_sources_amendments_and_answers_are_scoped_without_mutating_history():
    first = replace(_request(), source_message_ids=("shared",))
    second = replace(
        first,
        request_id="b",
        revisions=(RequestRevision(1, "old", (), first.items, "amend-b"),),
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
    assert [r["message_id"] for r in authorized_records(scoped, first)] == ["shared"]
    assert [r["message_id"] for r in authorized_records(scoped, second)] == ["shared", "amend-b", "answer-b"]


def test_batch_routing_control_and_its_result_are_excluded_from_execution_context():
    history = [
        _message("u", "user", "work"),
        _message("route", "assistant", "", tool_calls=[{"id": "c", "name": "submit_request_routing", "arguments": {}}]),
        _message("result", "tool", "batch receipt", tool_call_id="c", name="submit_request_routing"),
    ]
    scoped = assign_history_requests(history, (), {"u": "a", "route": "a"})
    assert [r["message_id"] for r in authorized_records(scoped, _request())] == ["u"]


def test_long_history_is_deduplicated_without_rolling_reselection():
    history = [_message(f"a{i}", "assistant", "answer" * 50, request_id="a") for i in range(21)]
    history.append(_message("current", "user", "Current question", request_id="a"))
    history.append(deepcopy(history[-1]))
    state = {"constraints": ["Never translate variable names"]}
    epoch = append_context(history, _request(), state, config_digest="fixed")
    assert len(epoch.source_digests) == 22
    assert epoch.messages[-1]["content"] == "Current question"
    assert "Never translate variable names" in epoch.messages[0]["content"]
    with pytest.raises(ContextBudgetExceeded):
        _budget(2000).require(epoch.messages)
    assert len(epoch.items) == 23  # Overflow does not silently drop old messages.


def test_schemas_output_and_protocol_reserve_are_jointly_budgeted():
    history = [_message("current", "user", "question")]
    tools = [LlmToolDefinition("lookup", "description" * 100, {"type": "object"})]
    budget = _budget(600)
    assert budget.require(history).total <= 600
    with pytest.raises(ContextBudgetExceeded) as error:
        budget.require(history, tools)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert error.value.usage.tool_schemas > 1000
    assert error.value.usage.output_reserve == 100
    assert error.value.usage.protocol_margin == 50


def test_large_tool_result_keeps_recoverable_reference_and_original_evidence():
    content = json.dumps({"evidence": "原始数据" * 5000}, ensure_ascii=False)
    source = _message("r", "tool", content, tool_call_id="c", name="translate")
    before = deepcopy(source)
    result = project_result(source)
    payload = json.loads(result["content"])
    assert payload["reference"]["digest"] == digest(content)
    assert payload["reference"]["message_id"] == "r"
    assert payload["reference"]["tool"] == "read_request_result"
    assert payload["material_only"] is True
    assert source == before


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
        protocol_groups(normalize_records(history))


def test_native_call_and_all_results_form_one_atomic_group():
    history = [
        _message(
            "a",
            "assistant",
            "",
            tool_calls=[
                {"id": "c1", "name": "x", "arguments": {}},
                {"id": "c2", "name": "x", "arguments": {}},
            ],
        ),
        _message("r2", "tool", "two", tool_call_id="c2"),
        _message("r1", "tool", "one", tool_call_id="c1"),
        _message("u", "user", "next"),
    ]
    assert protocol_groups(normalize_records(history)) == [[0, 1, 2], [3]]


def test_conflicting_message_ids_are_rejected():
    with pytest.raises(ValueError, match="Conflicting content"):
        normalize_records([_message("u", "user", "one"), _message("u", "user", "two")])


def test_default_budget_identifies_text_estimate_without_loading_model():
    usage = ContextBudget().measure([{"role": "user", "content": "中文"}])
    assert usage.estimator_label == "text-v2-estimated-25pct"
    assert 0 < usage.messages < len(json.dumps([{"role": "user", "content": "中文"}], ensure_ascii=False).encode())


def test_interleaved_late_result_is_inert_material_without_reordering_evidence():
    history = [
        _message("call", "assistant", "read", tool_calls=[{"id": "c", "name": "read", "arguments": {}}]),
        _message("input", "user", "new question"),
        _message("result", "tool", "old result", tool_call_id="c"),
    ]
    original = deepcopy(history)
    records = normalize_records(history)
    assert [m["message_id"] for m in records] == ["call", "input", "result"]
    assert [m["role"] for m in records] == ["assistant", "user", "assistant"]
    assert "historical_tool_calls" in records[0]["content"]
    assert records[-1]["_historical_result"]
    assert "tool_call_id" not in records[-1]
    assert protocol_groups(records) == [[0], [1], [2]]
    assert history == original


def test_foreign_last_tool_group_cannot_bypass_context_scope():
    history = [
        _message("input-a", "user", "Continue A", request_id="a"),
        _message("call-b", "assistant", "", request_id="b", tool_calls=[{"id": "b", "name": "read", "arguments": {}}]),
        _message("result-b", "tool", "B evidence", request_id="b", tool_call_id="b"),
    ]
    epoch = append_context(history, _request(), {}, config_digest="fixed")
    assert list(dict(epoch.source_digests)) == ["input-a"]
    assert "B evidence" not in json.dumps(epoch.messages)
