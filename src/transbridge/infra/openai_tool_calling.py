"""OpenAI Chat Completions adapter for provider-neutral native tool calls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import json
import logging
from typing import Any

from transbridge.infra.llm_tool_calling import (
    LlmToolCall,
    LlmToolDefinition,
    LlmToolProtocolError,
    LlmTurn,
    parse_tool_arguments,
    require_complete_tool_call,
)
from transbridge.infra.llm_usage import UsageAttempt, single_attempt_client
from transbridge.infra.prompt_cache import (
    extract_prompt_cache_directives,
    prepare_openai_chat_cache_request,
)

logger = logging.getLogger(__name__)

_PROMPT_CACHE_REJECTION_KEYWORDS = ("prompt_cache", "prompt cache", "cache_control", "cache")


@dataclass
class _PendingToolCall:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class _StreamAccumulator:
    text: str = ""
    stop_reason: str | None = None
    saw_event: bool = False

    def __post_init__(self) -> None:
        self.tool_calls: dict[int, _PendingToolCall] = {}


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _is_cache_rejection(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status not in (400, 422):
        return False
    message = getattr(exc, "message", None)
    text = message if isinstance(message, str) else str(exc)
    lowered = text.lower()
    return any(keyword in lowered for keyword in _PROMPT_CACHE_REJECTION_KEYWORDS)


def _openai_tools(tools: list[LlmToolDefinition]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        function = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        if tool.strict:
            function["strict"] = True
        converted.append({
            "type": "function",
            "function": function,
        })
    return converted


def _request_kwargs(
    owner: Any,
    messages: list[dict],
    max_tokens: int,
    tools: list[LlmToolDefinition],
) -> dict[str, Any]:
    request = prepare_openai_chat_cache_request(
        model=owner._model,
        base_url=owner._base_url,
        messages=_convert_messages(messages),
    )
    kwargs: dict[str, Any] = {
        "model": owner._model,
        "messages": request["messages"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": _openai_tools(tools),
        "tool_choice": "auto",
    }
    if request["request_options"]:
        kwargs["extra_body"] = dict(request["request_options"])
    if max_tokens > 0:
        kwargs["max_tokens"] = max_tokens
    if not tools:
        kwargs.pop("tools")
        kwargs.pop("tool_choice")
    return kwargs


def _clean_retry_kwargs(
    owner: Any,
    messages: list[dict],
    max_tokens: int,
    tools: list[LlmToolDefinition],
) -> dict[str, Any]:
    clean_messages, _ = extract_prompt_cache_directives(_convert_messages(messages))
    kwargs: dict[str, Any] = {
        "model": owner._model,
        "messages": clean_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": _openai_tools(tools),
        "tool_choice": "auto",
    }
    if max_tokens > 0:
        kwargs["max_tokens"] = max_tokens
    if not tools:
        kwargs.pop("tools")
        kwargs.pop("tool_choice")
    return kwargs


def _convert_messages(messages: list[dict]) -> list[dict[str, Any]]:
    """Map canonical tool history to OpenAI Chat Completions messages."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            converted.append({
                "role": "tool",
                "tool_call_id": str(message.get("tool_call_id", "")),
                "content": str(message.get("content", "")),
            })
            continue

        item = {
            key: deepcopy(value)
            for key, value in message.items()
            if key not in {"tool_calls", "provider_content", "display_summary", "is_error", "name"}
        }
        if role == "assistant" and message.get("tool_calls"):
            item["tool_calls"] = []
            for raw_call in message["tool_calls"]:
                call = raw_call if isinstance(raw_call, LlmToolCall) else LlmToolCall.from_dict(raw_call)
                item["tool_calls"].append({
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                })
        converted.append(item)
    return converted


def _merge_tool_delta(accumulator: _StreamAccumulator, delta: Any) -> None:
    index = _field(delta, "index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise LlmToolProtocolError("The model returned a tool-call delta without a valid index")

    pending = accumulator.tool_calls.setdefault(index, _PendingToolCall())
    call_id = _field(delta, "id") or ""
    if call_id:
        if pending.call_id and pending.call_id != call_id:
            raise LlmToolProtocolError(f"Tool-call index {index} changed id during streaming")
        pending.call_id = call_id

    function = _field(delta, "function")
    if function is None:
        return
    name = _field(function, "name") or ""
    if name:
        if pending.name and pending.name != name:
            raise LlmToolProtocolError(f"Tool-call index {index} changed name during streaming")
        pending.name = name
    arguments = _field(function, "arguments") or ""
    if not isinstance(arguments, str):
        raise LlmToolProtocolError(f"Tool-call index {index} returned non-text argument data")
    pending.arguments += arguments


def _consume_stream(stream: Any, chunk_callback, accumulator: _StreamAccumulator, attempt: UsageAttempt) -> None:
    for chunk in stream:
        accumulator.saw_event = True
        choices = _field(chunk, "choices", ()) or ()
        attempt.observe(
            _field(chunk, "usage"),
            final=not choices or any(_field(choice, "finish_reason") is not None for choice in choices),
        )
        if not choices:
            continue
        choice = choices[0]
        stop_reason = _field(choice, "finish_reason")
        if stop_reason is not None:
            accumulator.stop_reason = str(stop_reason)
        delta = _field(choice, "delta")
        if delta is None:
            continue

        text = _field(delta, "content") or ""
        if text:
            if not isinstance(text, str):
                raise LlmToolProtocolError("The model returned non-text content in a Chat Completions delta")
            accumulator.text += text
            chunk_callback(text)

        for tool_delta in _field(delta, "tool_calls", ()) or ():
            _merge_tool_delta(accumulator, tool_delta)
    if accumulator.stop_reason is not None and attempt.raw:
        attempt.final_received = True


def _finalize(accumulator: _StreamAccumulator) -> LlmTurn:
    if accumulator.stop_reason is None:
        raise LlmToolProtocolError("The model stream ended without a finish_reason")
    if accumulator.tool_calls and accumulator.stop_reason != "tool_calls":
        if accumulator.stop_reason in {"length", "max_tokens"}:
            require_complete_tool_call(call_id="", tool_name="", stop_reason=accumulator.stop_reason)
        reason = accumulator.stop_reason or "<missing>"
        raise LlmToolProtocolError(f"The model returned tool calls with unexpected finish_reason: {reason}")
    if not accumulator.tool_calls and accumulator.stop_reason == "tool_calls":
        raise LlmToolProtocolError("The model returned finish_reason=tool_calls without any tool calls")
    if accumulator.stop_reason in {"length", "max_tokens", "content_filter"}:
        raise LlmToolProtocolError(f"The model response was incomplete: {accumulator.stop_reason}")

    calls: list[LlmToolCall] = []
    for index in sorted(accumulator.tool_calls):
        pending = accumulator.tool_calls[index]
        require_complete_tool_call(
            call_id=pending.call_id,
            tool_name=pending.name,
            stop_reason=accumulator.stop_reason,
        )
        calls.append(
            LlmToolCall(
                id=pending.call_id,
                name=pending.name,
                arguments=parse_tool_arguments(
                    pending.arguments,
                    call_id=pending.call_id,
                    tool_name=pending.name,
                ),
            )
        )
    return LlmTurn(
        text=accumulator.text,
        tool_calls=tuple(calls),
        stop_reason=accumulator.stop_reason,
    )


def chat_stream_with_tools(
    owner: Any,
    messages: list[dict],
    max_tokens: int,
    tools: list[LlmToolDefinition],
    chunk_callback,
    *,
    usage_callback=None,
    purpose: str = "execution",
) -> LlmTurn:
    """Run one native OpenAI-compatible streaming tool-call turn.

    ``owner`` is an ``OpenAICompatibleClient``-shaped object. Keeping the SDK
    mechanics here lets the main client delegate without coupling the neutral
    tool-call contracts to OpenAI response models.
    """

    with owner._lock:
        client = single_attempt_client(owner._client)
        owner._active_requests += 1
    try:
        kwargs = _request_kwargs(owner, messages, max_tokens, tools)
        retry_of = None
        for index in range(2):
            accumulator = _StreamAccumulator()
            attempt = UsageAttempt("openai", owner._model, purpose, usage_callback, retry_of=retry_of)
            try:
                with client.chat.completions.create(**kwargs) as stream:
                    _consume_stream(stream, chunk_callback, accumulator, attempt)
                turn = _finalize(accumulator)
            except BaseException as exc:
                usage_rejected = _is_usage_rejection(exc)
                cache_rejected = _is_cache_rejection(exc)
                retry = index == 0 and not accumulator.saw_event and (usage_rejected or cache_rejected)
                reason = "usage_parameter_unsupported" if usage_rejected else "cache_parameter_unsupported"
                attempt.finish(
                    outcome="error" if isinstance(exc, Exception) else "cancelled",
                    degradation=reason if retry else None,
                )
                if not retry:
                    raise
                logger.warning("OpenAI optional parameters rejected; retrying once: %s", reason)
                retry_of = attempt.attempt_id
                if cache_rejected:
                    kwargs = _clean_retry_kwargs(owner, messages, max_tokens, tools)
                else:
                    kwargs = dict(kwargs)
                if usage_rejected:
                    kwargs.pop("stream_options", None)
            else:
                return replace(turn, usage=attempt.finish())
        raise AssertionError("Unreachable attempt exhaustion")
    finally:
        with owner._lock:
            owner._active_requests -= 1


def _is_usage_rejection(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(getattr(exc, "message", None) or exc).lower()
    return status in (400, 422) and any(key in text for key in ("stream_options", "include_usage"))


__all__ = ["chat_stream_with_tools"]
