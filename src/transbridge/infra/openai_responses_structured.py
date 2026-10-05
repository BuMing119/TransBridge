"""OpenAI-compatible Responses transport for native structured text."""

from __future__ import annotations

from transbridge.infra.llm_structured_outputs import (
    LlmOutputSchema,
    ensure_openai_responses_structured_output_completion,
    openai_responses_text_config,
    validate_structured_output,
)


def _value(source: object, name: str) -> object:
    return source.get(name) if isinstance(source, dict) else getattr(source, name, None)


def _refusal(response: object) -> object | None:
    for item in _value(response, "output") or ():
        for part in _value(item, "content") or ():
            if _value(part, "type") == "refusal":
                return _value(part, "refusal") or _value(part, "text") or "refused"
    return None


def build_structured_response_kwargs(
    *,
    model: str,
    messages: list[dict],
    output_schema: LlmOutputSchema,
    max_tokens: int,
    reasoning_patch,
    request_options: dict,
    stream: bool = False,
) -> dict:
    kwargs: dict = {
        "model": model,
        "input": messages,
        "text": openai_responses_text_config(output_schema),
        "store": False,
    }
    if stream:
        kwargs["stream"] = True
    if max_tokens > 0:
        kwargs["max_output_tokens"] = max_tokens
    extra_body = dict(request_options)
    if reasoning_patch is not None:
        configured = reasoning_patch.extra_body.get("reasoning")
        effort = reasoning_patch.standard.get("reasoning_effort")
        reasoning = (
            dict(configured) if isinstance(configured, dict) else {"effort": effort} if effort is not None else None
        )
        if reasoning is not None:
            kwargs["reasoning"] = reasoning
        extra_body.update({key: value for key, value in reasoning_patch.standard.items() if key != "reasoning_effort"})
        extra_body.update({key: value for key, value in reasoning_patch.extra_body.items() if key != "reasoning"})
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def validate_structured_response(
    response: object | None, output_schema: LlmOutputSchema, *, raw_text: str | None = None
) -> str:
    details = _value(response, "incomplete_details")
    ensure_openai_responses_structured_output_completion(
        status=_value(response, "status"),
        incomplete_reason=_value(details, "reason"),
        refusal=_refusal(response),
    )
    content = str(_value(response, "output_text") or "") if raw_text is None else raw_text
    return validate_structured_output(content, output_schema)


def consume_structured_response_stream(stream, chunk_callback) -> tuple[str, object | None]:
    full_text = ""
    terminal_response = None
    with stream:
        for event in stream:
            event_type = _value(event, "type")
            if event_type == "response.output_text.delta":
                delta = str(_value(event, "delta") or "")
                if delta:
                    full_text += delta
                    chunk_callback(delta)
            elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
                terminal_response = _value(event, "response")
    return full_text, terminal_response
