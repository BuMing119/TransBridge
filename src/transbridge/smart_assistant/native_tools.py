"""Build the native tool surface exposed to an LLM conversation round."""

from __future__ import annotations

from collections.abc import Iterable

from transbridge.infra.llm_tool_calling import (
    LlmToolDefinition,
    LlmToolProtocolError,
    LlmTurn,
)

from .tool_registry import ToolRegistry, ToolSpec

PROPOSE_PLAN_TOOL_NAME = "propose_plan"
CORE_TOOL_NAMES = ("get_app_state", "get_statistics", "get_tool_help")


def _propose_plan_definition() -> LlmToolDefinition:
    return LlmToolDefinition(
        name=PROPOSE_PLAN_TOOL_NAME,
        description=(
            "Submit a multi-step execution plan for user confirmation. Use it only when a task has "
            "multiple predictable steps, dependencies, or parallel work. This tool submits the plan "
            "but does not execute its steps."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A brief user-facing plan summary without hidden reasoning.",
                },
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Tool steps organized by dependency.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "A unique step number within the plan, starting at 1.",
                            },
                            "tool": {
                                "type": "string",
                                "minLength": 1,
                                "description": "The name of a loaded native tool.",
                            },
                            "args": {
                                "type": "object",
                                "description": "The argument object passed to the tool.",
                                "additionalProperties": True,
                            },
                            "depends_on": {
                                "type": "array",
                                "description": (
                                    "IDs of steps this step depends on; use an empty array when there are none."
                                ),
                                "items": {"type": "integer", "minimum": 1},
                                "uniqueItems": True,
                            },
                        },
                        "required": ["id", "tool", "args", "depends_on"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["summary", "steps"],
            "additionalProperties": False,
        },
        strict=False,
    )


def _normalize_namespaces(loaded_namespaces: Iterable[str] | str | None) -> tuple[str, ...]:
    if loaded_namespaces is None:
        return ()
    if isinstance(loaded_namespaces, str):
        values = loaded_namespaces.split(",")
    else:
        values = loaded_namespaces

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        namespace = str(value).strip()
        if namespace and namespace not in seen:
            seen.add(namespace)
            normalized.append(namespace)
    return tuple(normalized)


def _is_exposable(spec: ToolSpec | None) -> bool:
    return spec is not None and spec.available and not spec.deprecated


def build_native_tool_definitions(
    loaded_namespaces: Iterable[str] | str | None = (),
) -> tuple[LlmToolDefinition, ...]:
    """Return core/control tools plus tools from namespaces loaded in this session.

    Names are unique and ordering is deterministic: core tools, ``propose_plan``,
    then each requested namespace in caller-provided order. Unknown namespaces and
    unavailable/deprecated tools are ignored.
    """

    definitions: list[LlmToolDefinition] = []
    seen: set[str] = set()

    def add_spec(spec: ToolSpec | None) -> None:
        if not _is_exposable(spec) or spec.name in seen:
            return
        seen.add(spec.name)
        definitions.append(spec.to_llm_tool_definition())

    for name in CORE_TOOL_NAMES:
        add_spec(ToolRegistry.get(name))

    seen.add(PROPOSE_PLAN_TOOL_NAME)
    definitions.append(_propose_plan_definition())

    for namespace in _normalize_namespaces(loaded_namespaces):
        for spec in ToolRegistry.list_namespace(namespace):
            add_spec(spec)

    return tuple(definitions)


def turn_to_parsed_response(turn: LlmTurn) -> dict:
    """Map one native assistant turn onto the existing controller dispatch shape."""

    plan_calls = [call for call in turn.tool_calls if call.name == PROPOSE_PLAN_TOOL_NAME]
    if plan_calls and len(turn.tool_calls) != 1:
        raise LlmToolProtocolError("propose_plan cannot be mixed with business tool calls")

    summary = turn.text
    if not plan_calls:
        long_running = []
        for call in turn.tool_calls:
            spec = ToolRegistry.get(call.name)
            if spec is not None and spec.is_long_running:
                long_running.append(call.name)
        if long_running and len(turn.tool_calls) > 1:
            raise LlmToolProtocolError("A long-running tool call must be the only business call in its turn")
        steps = [
            {
                "id": index,
                "tool": call.name,
                "args": dict(call.arguments),
                "depends_on": [],
                "tool_call_id": call.id,
            }
            for index, call in enumerate(turn.tool_calls, start=1)
        ]
        return {
            "mode": "react",
            "thought": summary,
            "summary": summary,
            "steps": steps,
        }

    plan_call = plan_calls[0]
    plan_summary = plan_call.arguments.get("summary", "")
    raw_steps = plan_call.arguments.get("steps")
    if not isinstance(plan_summary, str):
        raise LlmToolProtocolError("propose_plan summary must be a string")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise LlmToolProtocolError("propose_plan steps must be a non-empty array")

    steps: list[dict] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise LlmToolProtocolError(f"propose_plan step {index + 1} must be an object")
        step_id = raw_step.get("id")
        tool_name = raw_step.get("tool")
        args = raw_step.get("args")
        depends_on = raw_step.get("depends_on")
        if not isinstance(step_id, int) or isinstance(step_id, bool) or step_id < 1:
            raise LlmToolProtocolError(f"propose_plan step {index + 1} has an invalid id")
        if not isinstance(tool_name, str) or not tool_name:
            raise LlmToolProtocolError(f"propose_plan step {index + 1} has an invalid tool name")
        if not isinstance(args, dict):
            raise LlmToolProtocolError(f"propose_plan step {index + 1} args must be an object")
        if not isinstance(depends_on, list) or any(
            not isinstance(value, int) or isinstance(value, bool) for value in depends_on
        ):
            raise LlmToolProtocolError(f"propose_plan step {index + 1} depends_on must contain integer ids")
        steps.append({
            "id": step_id,
            "tool": tool_name,
            "args": dict(args),
            "depends_on": list(depends_on),
        })

    _validate_plan_graph(steps)

    return {
        "mode": "plan",
        "thought": plan_summary,
        "summary": plan_summary,
        "steps": steps,
        "plan_call_id": plan_call.id,
    }


def _validate_plan_graph(steps: list[dict]) -> None:
    ids = [step["id"] for step in steps]
    if len(ids) != len(set(ids)):
        raise LlmToolProtocolError("propose_plan step ids must be unique")
    known = set(ids)
    adjacency: dict[int, list[int]] = {step_id: [] for step_id in ids}
    in_degree = dict.fromkeys(ids, 0)
    for step in steps:
        for dependency in step["depends_on"]:
            if dependency not in known:
                raise LlmToolProtocolError(f"propose_plan step {step['id']} depends on unknown step {dependency}")
            adjacency[dependency].append(step["id"])
            in_degree[step["id"]] += 1

    ready = [step_id for step_id, degree in in_degree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for dependent in adjacency[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
    if visited != len(steps):
        raise LlmToolProtocolError("propose_plan contains a cyclic dependency")


__all__ = [
    "CORE_TOOL_NAMES",
    "PROPOSE_PLAN_TOOL_NAME",
    "build_native_tool_definitions",
    "turn_to_parsed_response",
]
