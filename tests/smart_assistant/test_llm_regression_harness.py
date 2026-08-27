from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import runpy

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn

_HARNESS = runpy.run_path(str(Path(__file__).parent / "test_data" / "run_llm_regression.py"))


class _RecordingClient:
    def __init__(self, turns: list[LlmTurn]):
        self._turns = iter(turns)
        self.requests: list[dict] = []

    def chat_stream_with_tools(self, messages, max_tokens, tools, chunk_callback):
        self.requests.append({
            "messages": deepcopy(messages),
            "max_tokens": max_tokens,
            "tools": tuple(tool.name for tool in tools),
        })
        return next(self._turns)


def test_harness_loads_namespace_after_native_help_call():
    prompt = _HARNESS["build_prompt"]()
    client = _RecordingClient([
        LlmTurn(tool_calls=(LlmToolCall("help-1", "get_tool_help", {"namespace": "editor"}),)),
        LlmTurn(tool_calls=(LlmToolCall("call-1", "set_filters", {"stages": ["untranslated"]}),)),
    ])

    calls = _HARNESS["collect_native_tool_calls"](
        client,
        prompt,
        "筛选未翻译条目",
        max_tokens=500,
    )

    assert [call.name for call in calls] == ["get_tool_help", "set_filters"]
    assert "set_filters" not in client.requests[0]["tools"]
    assert "set_filters" in client.requests[1]["tools"]
    assert client.requests[1]["messages"][-1]["tool_call_id"] == "help-1"


def test_harness_does_not_parse_json_looking_text_as_tool_calls():
    prompt = _HARNESS["build_prompt"]()
    client = _RecordingClient([
        LlmTurn(text='{"mode":"react","steps":[{"tool":"set_filters"}]}'),
    ])

    calls = _HARNESS["collect_native_tool_calls"](
        client,
        prompt,
        "hello",
        max_tokens=500,
    )

    assert calls == []
    assert len(client.requests) == 1


def test_harness_infers_route_from_native_plan_step():
    _HARNESS["build_prompt"]()
    calls = [
        LlmToolCall(
            "plan-1",
            "propose_plan",
            {
                "summary": "解析后翻译",
                "steps": [
                    {"id": 1, "tool": "parse_esp", "args": {}, "depends_on": []},
                    {"id": 2, "tool": "start_translation", "args": {}, "depends_on": [1]},
                ],
            },
        )
    ]

    assert _HARNESS["infer_first_namespace"](calls) == "parser"
