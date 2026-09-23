"""The native state query cannot choose a request or be embedded in business plans."""

import pytest

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmToolProtocolError, LlmTurn
from transbridge.smart_assistant.native_tools import build_native_tool_definitions, turn_to_parsed_response
from transbridge.smart_assistant.state_query_protocol import STATE_RETRIEVAL_TOOL, parse_state_retrieval

ARGS = {"section": "items", "offset": 0, "limit": 2000, "expected_digest": ""}


def test_tool_is_available_only_in_execution_and_preserves_query_arguments():
    tool = next(t for t in build_native_tool_definitions(request_stage="execution") if t.name == STATE_RETRIEVAL_TOOL)
    assert "request_id" not in tool.input_schema["properties"]
    assert all(t.name != STATE_RETRIEVAL_TOOL for t in build_native_tool_definitions(request_stage="routing"))
    turn = LlmTurn(tool_calls=(LlmToolCall("q", STATE_RETRIEVAL_TOOL, ARGS),))
    assert turn_to_parsed_response(turn, request_stage="execution")["arguments"] == ARGS
    with pytest.raises(LlmToolProtocolError):
        turn_to_parsed_response(turn)


@pytest.mark.parametrize(
    "changes", [{"request_id": "other"}, {"section": "../state"}, {"offset": True}, {"offset": 1}, {"limit": 8001}]
)
def test_invalid_or_scope_selecting_arguments_are_rejected(changes):
    with pytest.raises(LlmToolProtocolError):
        parse_state_retrieval({**ARGS, **changes})
