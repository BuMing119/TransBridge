from __future__ import annotations

from copy import deepcopy

import pytest

from transbridge.infra.llm_tool_calling import (
    LlmToolCall,
    LlmToolDefinition,
    LlmToolProtocolError,
    LlmTurn,
)
from transbridge.smart_assistant.native_tools import (
    CORE_TOOL_NAMES,
    PROPOSE_PLAN_TOOL_NAME,
    build_native_tool_definitions,
    turn_to_parsed_response,
)
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec


def _spec(name: str, *, deprecated: bool = False, available: bool = True) -> ToolSpec:
    return ToolSpec(
        name=name,
        display_name=name,
        description=f"{name} description",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        deprecated=deprecated,
        available=available,
    )


@pytest.fixture
def isolated_registry(monkeypatch):
    core = {name: _spec(name) for name in CORE_TOOL_NAMES}
    namespaces = {
        "default": {**core, "default_extra": _spec("default_extra")},
        "parser": {
            "parse_file": _spec("parse_file"),
            "old_parser": _spec("old_parser", deprecated=True),
            "missing_parser": _spec("missing_parser", available=False),
        },
        "translator": {
            "start_translation": _spec("start_translation"),
            "parse_file": _spec("parse_file"),
        },
    }
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", namespaces)
    return namespaces


def test_tool_spec_exports_provider_neutral_definition() -> None:
    spec = _spec("sample")
    original = deepcopy(spec.parameters)

    definition = spec.to_llm_tool_definition()

    assert isinstance(definition, LlmToolDefinition)
    assert definition.name == "sample"
    assert definition.description == "sample description"
    assert definition.input_schema == original
    assert definition.input_schema is not spec.parameters
    assert definition.strict is False


def test_first_round_exposes_only_core_and_plan(isolated_registry) -> None:
    definitions = build_native_tool_definitions()

    assert [definition.name for definition in definitions] == [
        *CORE_TOOL_NAMES,
        PROPOSE_PLAN_TOOL_NAME,
    ]
    assert all(definition.strict is False for definition in definitions)


def test_loaded_namespaces_append_available_nondeprecated_tools_once(isolated_registry) -> None:
    definitions = build_native_tool_definitions(["parser", "translator", "parser"])

    names = [definition.name for definition in definitions]
    assert names == [
        *CORE_TOOL_NAMES,
        PROPOSE_PLAN_TOOL_NAME,
        "parse_file",
        "start_translation",
    ]
    assert "old_parser" not in names
    assert "missing_parser" not in names


def test_loaded_namespaces_accept_comma_separated_string(isolated_registry) -> None:
    names = [definition.name for definition in build_native_tool_definitions(" default, parser,unknown ")]

    assert names == [
        *CORE_TOOL_NAMES,
        PROPOSE_PLAN_TOOL_NAME,
        "default_extra",
        "parse_file",
    ]


def test_propose_plan_has_dag_schema_and_is_not_strict(isolated_registry) -> None:
    definition = next(item for item in build_native_tool_definitions() if item.name == PROPOSE_PLAN_TOOL_NAME)

    assert definition.strict is False
    assert definition.input_schema["required"] == ["summary", "steps"]
    step_schema = definition.input_schema["properties"]["steps"]["items"]
    assert step_schema["required"] == ["id", "tool", "args", "depends_on"]
    assert step_schema["properties"]["args"]["additionalProperties"] is True
    assert step_schema["properties"]["depends_on"]["items"]["type"] == "integer"


def test_turn_to_parsed_response_maps_business_calls_to_react_steps() -> None:
    parsed = turn_to_parsed_response(
        LlmTurn(
            text="先检查状态",
            tool_calls=(
                LlmToolCall("call_1", "get_app_state", {}),
                LlmToolCall("call_2", "get_statistics", {"scope": "all"}),
            ),
        )
    )

    assert parsed["mode"] == "react"
    assert parsed["summary"] == "先检查状态"
    assert parsed["steps"] == [
        {
            "id": 1,
            "tool": "get_app_state",
            "args": {},
            "depends_on": [],
            "tool_call_id": "call_1",
        },
        {
            "id": 2,
            "tool": "get_statistics",
            "args": {"scope": "all"},
            "depends_on": [],
            "tool_call_id": "call_2",
        },
    ]


def test_turn_to_parsed_response_maps_propose_plan() -> None:
    parsed = turn_to_parsed_response(
        LlmTurn(
            tool_calls=(
                LlmToolCall(
                    "call_plan",
                    PROPOSE_PLAN_TOOL_NAME,
                    {
                        "summary": "先解析再翻译",
                        "steps": [
                            {"id": 1, "tool": "parse_file", "args": {"path": "a.esp"}, "depends_on": []},
                            {"id": 2, "tool": "start_translation", "args": {}, "depends_on": [1]},
                        ],
                    },
                ),
            ),
        )
    )

    assert parsed["mode"] == "plan"
    assert parsed["summary"] == "先解析再翻译"
    assert parsed["plan_call_id"] == "call_plan"
    assert parsed["steps"][1]["depends_on"] == [1]


def test_turn_to_parsed_response_rejects_mixed_plan_and_business_calls() -> None:
    turn = LlmTurn(
        tool_calls=(
            LlmToolCall("call_plan", PROPOSE_PLAN_TOOL_NAME, {"summary": "计划", "steps": []}),
            LlmToolCall("call_tool", "get_statistics", {}),
        )
    )

    with pytest.raises(LlmToolProtocolError, match="cannot be mixed"):
        turn_to_parsed_response(turn)


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [
                {"id": 1, "tool": "a", "args": {}, "depends_on": []},
                {"id": 1, "tool": "b", "args": {}, "depends_on": []},
            ],
            "unique",
        ),
        ([{"id": 1, "tool": "a", "args": {}, "depends_on": [9]}], "unknown step"),
        (
            [
                {"id": 1, "tool": "a", "args": {}, "depends_on": [2]},
                {"id": 2, "tool": "b", "args": {}, "depends_on": [1]},
            ],
            "cyclic",
        ),
    ],
)
def test_turn_to_parsed_response_rejects_invalid_plan_graph(steps, message) -> None:
    turn = LlmTurn(tool_calls=(LlmToolCall("plan", PROPOSE_PLAN_TOOL_NAME, {"summary": "bad", "steps": steps}),))
    with pytest.raises(LlmToolProtocolError, match=message):
        turn_to_parsed_response(turn)
