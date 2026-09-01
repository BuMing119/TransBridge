from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from transbridge.infra.llm_client import OpenAICompatibleClient
from transbridge.infra.llm_structured_outputs import (
    STRUCTURED_OUTPUT_METADATA_KEY,
    LlmOutputSchema,
    LlmStructuredOutputInvalidResponseError,
    LlmStructuredOutputRefusalError,
    LlmStructuredOutputTruncatedError,
    LlmStructuredOutputUnsupportedError,
    attach_structured_output_directive,
)

_SCHEMA_JSON = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
_OUTPUT_SCHEMA = LlmOutputSchema("test_result", _SCHEMA_JSON)


def _messages() -> list[dict]:
    return [
        attach_structured_output_directive(
            {"role": "system", "content": "Return the result."},
            _OUTPUT_SCHEMA,
        ),
        {"role": "user", "content": "Translate this."},
    ]


def _response(
    content: str = '{"value":"ok"}',
    *,
    status: str = "completed",
    incomplete_reason: str | None = None,
    refusal: object | None = None,
):
    part = (
        SimpleNamespace(type="refusal", refusal=refusal)
        if refusal is not None
        else SimpleNamespace(type="output_text", text=content)
    )
    return SimpleNamespace(
        output_text=content,
        status=status,
        incomplete_details=(SimpleNamespace(reason=incomplete_reason) if incomplete_reason is not None else None),
        output=[SimpleNamespace(type="message", content=[part])],
    )


def _event(event_type: str, *, delta: str = "", response: object | None = None):
    return SimpleNamespace(type=event_type, delta=delta, response=response)


class _Stream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def __iter__(self):
        return iter(self._chunks)


def _client() -> OpenAICompatibleClient:
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._api_key = "test"
    client._base_url = "https://gateway.example/v1"
    client._model = "test-model"
    client._max_retries = 0
    client._lock = threading.Lock()
    client._http_client = MagicMock()
    client._client = MagicMock()
    client._active_requests = 0
    return client


def _expected_text_config() -> dict:
    return {
        "format": {
            "type": "json_schema",
            "name": "test_result",
            "schema": _SCHEMA_JSON,
        }
    }


def test_chat_sends_native_schema_strips_metadata_and_returns_original_json() -> None:
    client = _client()
    raw = '{ "value": "translated" }'
    client._client.responses.create.return_value = _response(raw)

    result = client.chat(_messages(), max_tokens=64)

    assert result == raw
    kwargs = client._client.responses.create.call_args.kwargs
    assert kwargs == {
        "model": "test-model",
        "input": [
            {"role": "system", "content": "Return the result."},
            {"role": "user", "content": "Translate this."},
        ],
        "text": _expected_text_config(),
        "store": False,
        "max_output_tokens": 64,
    }
    assert all(STRUCTURED_OUTPUT_METADATA_KEY not in message for message in kwargs["input"])
    client._client.chat.completions.create.assert_not_called()
    assert client._active_requests == 0


def test_plain_chat_keeps_legacy_request_and_response_behavior() -> None:
    client = _client()
    client._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="plain"))]
    )

    assert client.chat([{"role": "user", "content": "hello"}], max_tokens=0) == "plain"

    kwargs = client._client.chat.completions.create.call_args.kwargs
    assert "response_format" not in kwargs
    assert "max_tokens" not in kwargs


def test_stream_sends_native_schema_validates_completion_and_preserves_callbacks() -> None:
    client = _client()
    terminal = _response('{"value":"ok"}')
    client._client.responses.create.return_value = _Stream([
        _event("response.output_text.delta", delta='{"value":'),
        _event("response.output_text.delta", delta='"ok"}'),
        _event("response.completed", response=terminal),
    ])
    received: list[str] = []

    result = client.chat_stream(_messages(), 32, received.append)

    assert result == '{"value":"ok"}'
    assert received == ['{"value":', '"ok"}']
    kwargs = client._client.responses.create.call_args.kwargs
    assert kwargs["text"] == _expected_text_config()
    assert kwargs["stream"] is True
    assert kwargs["store"] is False
    client._client.chat.completions.create.assert_not_called()
    assert client._active_requests == 0


class _ProviderError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def test_cache_rejection_retry_preserves_schema_reasoning_and_token_limit() -> None:
    client = _client()
    client._client.responses.create.side_effect = [
        _ProviderError("prompt_cache_options unsupported"),
        _response(),
    ]
    reasoning_patch = SimpleNamespace(
        standard={"reasoning_effort": "low"},
        extra_body={"reasoning": {"effort": "low"}},
    )
    prepared = {
        "messages": [
            {"role": "system", "content": "Return the result."},
            {"role": "user", "content": "Translate this."},
        ],
        "request_options": {"prompt_cache_key": "cache-key"},
    }

    with patch("transbridge.infra.prompt_cache.prepare_openai_chat_cache_request", return_value=prepared):
        result = client._chat(_messages(), 48, reasoning_patch=reasoning_patch)

    assert result == '{"value":"ok"}'
    first, second = (call.kwargs for call in client._client.responses.create.call_args_list)
    assert first["text"] == second["text"] == _expected_text_config()
    assert first["reasoning"] == second["reasoning"] == {"effort": "low"}
    assert first["max_output_tokens"] == second["max_output_tokens"] == 48
    assert first["extra_body"] == {"prompt_cache_key": "cache-key"}
    assert "extra_body" not in second


def test_stream_cache_rejection_retry_preserves_schema_and_reasoning() -> None:
    client = _client()
    terminal = _response()
    client._client.responses.create.side_effect = [
        _ProviderError("prompt_cache_options unsupported"),
        _Stream([
            _event("response.output_text.delta", delta='{"value":"ok"}'),
            _event("response.completed", response=terminal),
        ]),
    ]
    reasoning_patch = SimpleNamespace(
        standard={"reasoning_effort": "low"},
        extra_body={"reasoning": {"effort": "low"}},
    )
    prepared = {
        "messages": [
            {"role": "system", "content": "Return the result."},
            {"role": "user", "content": "Translate this."},
        ],
        "request_options": {"prompt_cache_key": "cache-key"},
    }

    with patch("transbridge.infra.prompt_cache.prepare_openai_chat_cache_request", return_value=prepared):
        result = client._chat_stream(_messages(), 48, lambda _text: None, reasoning_patch=reasoning_patch)

    assert result == '{"value":"ok"}'
    first, second = (call.kwargs for call in client._client.responses.create.call_args_list)
    assert first["text"] == second["text"] == _expected_text_config()
    assert first["reasoning"] == second["reasoning"] == {"effort": "low"}
    assert first["max_output_tokens"] == second["max_output_tokens"] == 48
    assert "prompt_cache_key" in first["extra_body"]
    assert "extra_body" not in second


@pytest.mark.parametrize(
    ("status", "incomplete_reason", "refusal", "error_type"),
    [
        ("completed", None, "I cannot comply", LlmStructuredOutputRefusalError),
        ("incomplete", "max_output_tokens", None, LlmStructuredOutputTruncatedError),
        ("incomplete", "content_filter", None, LlmStructuredOutputInvalidResponseError),
        ("failed", None, None, LlmStructuredOutputInvalidResponseError),
    ],
)
def test_chat_rejects_non_complete_structured_responses(status, incomplete_reason, refusal, error_type) -> None:
    client = _client()
    client._client.responses.create.return_value = _response(
        status=status,
        incomplete_reason=incomplete_reason,
        refusal=refusal,
    )

    with pytest.raises(error_type):
        client.chat(_messages(), max_tokens=32)

    assert client._active_requests == 0


def test_stream_rejects_invalid_json_after_complete_stop() -> None:
    client = _client()
    client._client.responses.create.return_value = _Stream([
        _event("response.output_text.delta", delta='{"value":'),
        _event("response.completed", response=_response('{"value":')),
    ])

    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        client.chat_stream(_messages(), 32, lambda _text: None)


@pytest.mark.parametrize(
    ("event_type", "status", "incomplete_reason", "refusal", "error_type"),
    [
        ("response.completed", "completed", None, "I cannot comply", LlmStructuredOutputRefusalError),
        ("response.incomplete", "incomplete", "max_output_tokens", None, LlmStructuredOutputTruncatedError),
        ("response.incomplete", "incomplete", "content_filter", None, LlmStructuredOutputInvalidResponseError),
    ],
)
def test_stream_rejects_non_complete_structured_responses(
    event_type,
    status,
    incomplete_reason,
    refusal,
    error_type,
) -> None:
    client = _client()
    terminal = _response(status=status, incomplete_reason=incomplete_reason, refusal=refusal)
    client._client.responses.create.return_value = _Stream([
        _event("response.output_text.delta", delta='{"value":"ok"}'),
        _event(event_type, response=terminal),
    ])

    with pytest.raises(error_type):
        client.chat_stream(_messages(), 32, lambda _text: None)


def test_explicit_responses_schema_rejection_is_classified_as_unsupported() -> None:
    client = _client()
    client._client.responses.create.side_effect = _ProviderError("text.format json_schema is unavailable now")

    with pytest.raises(LlmStructuredOutputUnsupportedError) as exc_info:
        client.chat(_messages(), max_tokens=32)

    assert exc_info.value.__cause__ is not None
