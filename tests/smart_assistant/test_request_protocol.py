"""Control turns must never enter the ordinary business-step dispatch path."""

import pytest

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmToolProtocolError, LlmTurn
from transbridge.smart_assistant.native_tools import build_native_tool_definitions, turn_to_parsed_response
from transbridge.smart_assistant.request_protocol import (
    CONTROL_TOOLS,
    COVERAGE_TOOL,
    HISTORY_RETRIEVAL_TOOL,
    RETRIEVAL_TOOL,
    ROUTING_TOOL,
    coverage_definition,
    history_retrieval_definition,
    retrieval_definition,
    routing_definition,
)


def _turn(name, arguments=None, *, text="", stop_reason="tool_calls"):
    return LlmTurn(text=text, tool_calls=(LlmToolCall("control-1", name, arguments or {}),), stop_reason=stop_reason)


def test_routing_stage_exposes_only_routing_control_despite_loaded_business_namespaces():
    definitions = build_native_tool_definitions(("translator", "writer"), request_stage="routing")
    assert [definition.name for definition in definitions] == [ROUTING_TOOL]
    schema = definitions[0].input_schema
    assert schema["additionalProperties"] is False
    assert schema["properties"]["directives"]["items"]["additionalProperties"] is False
    assert "request_id" not in schema["properties"]["directives"]["items"]["properties"]


def test_execution_stage_adds_coverage_and_retrieval_but_not_routing():
    names = [definition.name for definition in build_native_tool_definitions(request_stage="execution")]
    assert COVERAGE_TOOL in names and RETRIEVAL_TOOL in names and HISTORY_RETRIEVAL_TOOL in names
    assert ROUTING_TOOL not in names
    assert len(names) == len(set(names))
    assert "propose_plan" in names
    legacy_names = [definition.name for definition in build_native_tool_definitions()]
    assert not set(legacy_names).intersection(CONTROL_TOOLS)


@pytest.mark.parametrize(
    "definition", [routing_definition(), coverage_definition(), retrieval_definition(), history_retrieval_definition()]
)
def test_control_schemas_close_unknown_top_level_fields(definition):
    assert definition.input_schema["type"] == "object"
    assert definition.input_schema["additionalProperties"] is False
    assert set(definition.input_schema["required"]) <= definition.input_schema["properties"].keys()


def test_complete_answer_control_preserves_answer_text_and_has_no_business_steps():
    arguments = {"item_ids": ["i1"], "dispositions": {"i1": "answered"}}
    parsed = turn_to_parsed_response(_turn(COVERAGE_TOOL, arguments, text="完整回答"), request_stage="execution")
    assert parsed["mode"] == "request_control"
    assert parsed["steps"] == []
    assert parsed["summary"] == "完整回答"
    assert parsed["arguments"] == arguments
    assert parsed["tool_call_id"] == "control-1"


@pytest.mark.parametrize("control", sorted(CONTROL_TOOLS))
def test_control_without_admitted_request_stage_is_rejected(control):
    with pytest.raises(LlmToolProtocolError, match="admitted request stage"):
        turn_to_parsed_response(_turn(control))


@pytest.mark.parametrize("business", ["get_statistics", "start_translation", "propose_plan"])
def test_routing_never_dispatches_business_calls(business):
    with pytest.raises(LlmToolProtocolError, match="exactly one"):
        turn_to_parsed_response(_turn(business), request_stage="routing")


@pytest.mark.parametrize("control", sorted(CONTROL_TOOLS))
def test_control_and_business_calls_cannot_mix(control):
    turn = LlmTurn(tool_calls=(LlmToolCall("control", control, {}), LlmToolCall("business", "get_statistics", {})))
    with pytest.raises(LlmToolProtocolError):
        turn_to_parsed_response(turn, request_stage="routing" if control == ROUTING_TOOL else "execution")


@pytest.mark.parametrize("control", sorted(CONTROL_TOOLS))
def test_control_cannot_be_hidden_as_plan_step(control):
    turn = _turn(
        "propose_plan",
        {
            "summary": "plan",
            "steps": [
                {
                    "id": 1,
                    "tool": control,
                    "args": {},
                    "depends_on": [],
                }
            ],
        },
    )
    with pytest.raises(LlmToolProtocolError, match="embedded in a business plan"):
        turn_to_parsed_response(turn, request_stage="execution")


@pytest.mark.parametrize("reason", ["length", "max_tokens", "error", "cancelled"])
def test_interrupted_coverage_never_commits_answer(reason):
    with pytest.raises(LlmToolProtocolError, match="Incomplete"):
        turn_to_parsed_response(_turn(COVERAGE_TOOL, stop_reason=reason), request_stage="execution")


def test_execution_cannot_reclassify_inputs_through_routing_tool():
    with pytest.raises(LlmToolProtocolError, match="unavailable"):
        turn_to_parsed_response(_turn(ROUTING_TOOL), request_stage="execution")


def test_routing_requires_control_even_when_model_returns_plain_text_only():
    with pytest.raises(LlmToolProtocolError, match="exactly one"):
        turn_to_parsed_response(LlmTurn(text="I have created the request"), request_stage="routing")


def test_history_control_is_material_query_with_no_business_steps():
    args = {"message_id": "source", "offset": 0, "limit": 2000}
    parsed = turn_to_parsed_response(_turn(HISTORY_RETRIEVAL_TOOL, args), request_stage="execution")
    assert parsed["control"] == HISTORY_RETRIEVAL_TOOL
    assert parsed["steps"] == [] and parsed["arguments"] == args


@pytest.mark.parametrize(
    "changes",
    [{"path": "/private"}, {"request_id": "other"}, {"offset": True}, {"limit": 8001}, {"message_id": []}],
)
def test_history_control_rejects_owner_path_and_invalid_paging(changes):
    args = {"message_id": "source", "offset": 0, "limit": 2000, **changes}
    with pytest.raises(LlmToolProtocolError, match="history retrieval"):
        turn_to_parsed_response(_turn(HISTORY_RETRIEVAL_TOOL, args), request_stage="execution")
