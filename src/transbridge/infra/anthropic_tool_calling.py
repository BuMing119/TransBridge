"""Anthropic Messages protocol adapter for native client-side tool calling."""

from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any

from transbridge.infra.llm_tool_calling import (
    LlmToolCall,
    LlmToolDefinition,
    LlmToolProtocolError,
    LlmTurn,
    require_complete_tool_call,
)
from transbridge.infra.prompt_cache import build_anthropic_system_blocks

logger = logging.getLogger(__name__)

_CACHE_REJECTION_KEYWORDS = ("prompt_cache", "prompt cache", "cache_control", "cache")


def chat_stream_with_tools(
    owner: Any,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[LlmToolDefinition],
    chunk_callback,
) -> LlmTurn:
    """Stream one Anthropic turn and return its complete native tool calls.

    ``owner`` is an ``AnthropicClient``-compatible object. Keeping the protocol
    conversion here lets the client retain its existing cancellation and request
    accounting without leaking Anthropic SDK objects into the public contract.
    """

    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("Anthropic tool calling requires a positive max_tokens value")

    with owner._lock:
        client = owner._client
        owner._active_requests += 1

    saw_event = False

    def emit_text(text: str) -> None:
        if text:
            chunk_callback(text)

    def mark_event() -> None:
        nonlocal saw_event
        saw_event = True

    try:
        system_blocks, non_system_messages = build_anthropic_system_blocks(messages, model=owner._model)
        kwargs = _request_kwargs(
            owner,
            system_blocks,
            _convert_messages(non_system_messages),
            tools,
            max_tokens,
        )
        try:
            return _run_stream(client, kwargs, emit_text, mark_event)
        except Exception as exc:
            if not saw_event and _is_cache_rejection(exc) and _has_cache_control(system_blocks):
                no_cache_system, no_cache_messages = build_anthropic_system_blocks(
                    messages,
                    model=owner._model,
                    enable_cache=False,
                )
                retry_kwargs = _request_kwargs(
                    owner,
                    no_cache_system,
                    _convert_messages(no_cache_messages),
                    tools,
                    max_tokens,
                )
                logger.warning(
                    "Anthropic tool stream cache parameters rejected (%s); retrying without cache: model=%s",
                    exc,
                    owner._model,
                )
                return _run_stream(client, retry_kwargs, emit_text, mark_event)
            if not saw_event and system_blocks and _is_system_blocks_unsupported(exc):
                retry_kwargs = dict(kwargs)
                retry_kwargs["system"] = _system_text(system_blocks)
                logger.warning(
                    "Anthropic tool stream does not support system content blocks; retrying with text: model=%s",
                    owner._model,
                )
                return _run_stream(client, retry_kwargs, emit_text, mark_event)
            raise
    finally:
        with owner._lock:
            owner._active_requests -= 1


def _request_kwargs(
    owner: Any,
    system_blocks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tools: list[LlmToolDefinition],
    max_tokens: int,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": owner._model,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": [_convert_tool_definition(tool) for tool in tools],
    }
    if system_blocks:
        kwargs["system"] = system_blocks
    return kwargs


def _convert_tool_definition(tool: LlmToolDefinition) -> dict[str, Any]:
    converted: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": deepcopy(tool.input_schema),
    }
    if tool.strict:
        converted["strict"] = True
    return converted


def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map the shared history representation to Anthropic content blocks."""

    converted: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        if role == "system":
            index += 1
            continue
        if role == "tool":
            result_blocks: list[dict[str, Any]] = []
            while index < len(messages) and messages[index].get("role") == "tool":
                result_blocks.append(_tool_result_block(messages[index]))
                index += 1

            # Anthropic requires tool_result blocks first. A following user text
            # can share this message, which also preserves role alternation.
            if index < len(messages) and messages[index].get("role") == "user":
                user_blocks = _content_blocks(messages[index].get("content", ""))
                result_blocks.extend(block for block in user_blocks if block.get("type") != "tool_result")
                index += 1
            converted.append({"role": "user", "content": result_blocks})
            continue
        if role == "assistant":
            converted.append({"role": "assistant", "content": _assistant_blocks(message)})
        elif role == "user":
            blocks = _content_blocks(message.get("content", ""))
            tool_results = [block for block in blocks if block.get("type") == "tool_result"]
            other_blocks = [block for block in blocks if block.get("type") != "tool_result"]
            converted.append({"role": "user", "content": tool_results + other_blocks})
        index += 1
    return converted


def _assistant_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    provider_content = message.get("provider_content")
    if isinstance(provider_content, list) and provider_content:
        return [deepcopy(block) for block in provider_content if isinstance(block, dict)]

    blocks = _content_blocks(message.get("content", ""))
    for raw_call in message.get("tool_calls") or ():
        if isinstance(raw_call, LlmToolCall):
            call = raw_call
        elif isinstance(raw_call, dict):
            call = LlmToolCall.from_dict(raw_call)
        else:
            raise LlmToolProtocolError("Assistant tool_calls entries must be objects")
        blocks.append({
            "type": "tool_use",
            "id": call.id,
            "name": call.name,
            "input": deepcopy(call.arguments),
        })
    return blocks


def _tool_result_block(message: dict[str, Any]) -> dict[str, Any]:
    call_id = str(message.get("tool_call_id") or message.get("id") or "")
    if not call_id:
        raise LlmToolProtocolError("Tool result is missing tool_call_id")
    content = message.get("content", "")
    if not isinstance(content, (str, list)):
        content = json.dumps(content, ensure_ascii=False, default=str)
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": deepcopy(content),
    }
    if bool(message.get("is_error", False)):
        block["is_error"] = True
    return block


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return [deepcopy(block) for block in content if isinstance(block, dict)]
    if content is None:
        return []
    return [{"type": "text", "text": str(content)}]


def _run_stream(client: Any, kwargs: dict[str, Any], chunk_callback, event_callback=lambda: None) -> LlmTurn:
    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            event_callback()
            text = _event_text_delta(event)
            if text:
                chunk_callback(text)
        message = stream.get_final_message()
    return _turn_from_message(message)


def _event_text_delta(event: Any) -> str:
    if _field(event, "type") != "content_block_delta":
        return ""
    delta = _field(event, "delta")
    if _field(delta, "type") != "text_delta":
        return ""
    text = _field(delta, "text")
    return text if isinstance(text, str) else ""


def _turn_from_message(message: Any) -> LlmTurn:
    stop_reason = _field(message, "stop_reason")
    if stop_reason is not None:
        stop_reason = str(stop_reason)
    else:
        raise LlmToolProtocolError("The model stream ended without a stop_reason")

    text_parts: list[str] = []
    tool_calls: list[LlmToolCall] = []
    provider_content: list[dict[str, Any]] = []
    for block in _field(message, "content") or ():
        raw_block = _block_dict(block)
        provider_content.append(raw_block)
        block_type = _field(block, "type")
        if block_type == "text":
            text = _field(block, "text")
            if isinstance(text, str):
                text_parts.append(text)
        elif block_type == "tool_use":
            call_id = str(_field(block, "id") or "")
            name = str(_field(block, "name") or "")
            require_complete_tool_call(call_id=call_id, tool_name=name, stop_reason=stop_reason)
            arguments = _field(block, "input")
            if not isinstance(arguments, dict):
                raise LlmToolProtocolError(f"Tool call {call_id or '<missing>'} input must be a JSON object")
            tool_calls.append(LlmToolCall(id=call_id, name=name, arguments=deepcopy(arguments)))

    if tool_calls and stop_reason != "tool_use":
        reason = stop_reason or "<missing>"
        raise LlmToolProtocolError(f"The model returned tool calls with unexpected stop_reason: {reason}")
    if not tool_calls and stop_reason == "tool_use":
        raise LlmToolProtocolError("The model returned stop_reason=tool_use without any tool calls")
    if stop_reason in {"max_tokens", "model_context_window_exceeded", "refusal"}:
        raise LlmToolProtocolError(f"The model response was incomplete: {stop_reason}")

    return LlmTurn(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        stop_reason=stop_reason,
        provider_content=tuple(provider_content),
    )


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return deepcopy(block)
    model_dump = getattr(block, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    data = vars(block) if hasattr(block, "__dict__") else {}
    return {key: deepcopy(value) for key, value in data.items() if not key.startswith("_") and value is not None}


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _has_cache_control(system_blocks: list[dict[str, Any]]) -> bool:
    return any(isinstance(block, dict) and "cache_control" in block for block in system_blocks)


def _is_cache_rejection(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) not in (400, 422):
        return False
    message = getattr(exc, "message", None)
    text = message if isinstance(message, str) else str(exc)
    lowered = text.lower()
    return any(keyword in lowered for keyword in _CACHE_REJECTION_KEYWORDS)


def _is_system_blocks_unsupported(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) is None and "system" in str(exc).lower()


def _system_text(system_blocks: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(block.get("text", "")) for block in system_blocks if isinstance(block, dict) and block.get("type") == "text"
    )


__all__ = ["chat_stream_with_tools"]
