"""Provider-neutral contracts for native LLM tool calling."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


class LlmToolProtocolError(ValueError):
    """A Provider returned an incomplete or malformed tool call."""


@dataclass(frozen=True)
class LlmToolDefinition:
    """A function tool definition independent from a Provider SDK."""

    name: str
    description: str
    input_schema: dict[str, Any]
    strict: bool = False


@dataclass(frozen=True)
class LlmToolCall:
    """One complete tool call emitted by a model."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LlmToolCall:
        arguments = data.get("arguments", {})
        if not isinstance(arguments, dict):
            raise LlmToolProtocolError("Tool call arguments must be a JSON object")
        return cls(id=str(data.get("id", "")), name=str(data.get("name", "")), arguments=arguments)


@dataclass(frozen=True)
class LlmTurn:
    """One complete assistant turn, including native tool calls when present."""

    text: str = ""
    tool_calls: tuple[LlmToolCall, ...] = ()
    stop_reason: str | None = None
    provider_content: tuple[dict[str, Any], ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        call_ids = [call.id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise LlmToolProtocolError("The model returned duplicate tool call ids")

    def to_assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        if self.provider_content:
            message["provider_content"] = [dict(block) for block in self.provider_content]
        return message


def parse_tool_arguments(raw: str, *, call_id: str, tool_name: str) -> dict[str, Any]:
    """Parse a complete Provider argument string and reject partial/non-object JSON."""

    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise LlmToolProtocolError(
            f"Tool call {call_id or '<missing>'} ({tool_name or '<missing>'}) returned invalid JSON arguments"
        ) from exc
    if not isinstance(value, dict):
        raise LlmToolProtocolError(
            f"Tool call {call_id or '<missing>'} ({tool_name or '<missing>'}) arguments must be a JSON object"
        )
    return value


def require_complete_tool_call(*, call_id: str, tool_name: str, stop_reason: str | None) -> None:
    """Reject calls that cannot be safely associated or were cut off by the Provider."""

    if stop_reason in {"length", "max_tokens"}:
        raise LlmToolProtocolError("The model response ended before the tool call was complete")
    if not call_id or not tool_name:
        raise LlmToolProtocolError("The model returned a tool call without an id or name")
