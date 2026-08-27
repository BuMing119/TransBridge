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
    finish_reason: str | None = "stop",
    refusal: object | None = None,
):
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _chunk(
    content: str = "",
    *,
    finish_reason: str | None = None,
    refusal: object | None = None,
):
    delta = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


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


def _expected_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "test_result",
            "schema": _SCHEMA_JSON,
            "strict": True,
        },
    }


def test_chat_sends_native_schema_strips_metadata_and_returns_original_json() -> None:
    client = _client()
    raw = '{ "value": "translated" }'
    client._client.chat.completions.create.return_value = _response(raw)

    result = client.chat(_messages(), max_tokens=64)

    assert result == raw
    kwargs = client._client.chat.completions.create.call_args.kwargs
    assert kwargs == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "Return the result."},
            {"role": "user", "content": "Translate this."},
        ],
        "response_format": _expected_response_format(),
        "max_tokens": 64,
    }
    assert all(STRUCTURED_OUTPUT_METADATA_KEY not in message for message in kwargs["messages"])
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
    client._client.chat.completions.create.return_value = _Stream([
        _chunk('{"value":'),
        _chunk('"ok"}'),
        _chunk(finish_reason="stop"),
    ])
    received: list[str] = []

    result = client.chat_stream(_messages(), 32, received.append)

    assert result == '{"value":"ok"}'
    assert received == ['{"value":', '"ok"}']
    kwargs = client._client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == _expected_response_format()
    assert kwargs["stream"] is True
    assert client._active_requests == 0


class _ProviderError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def test_cache_rejection_retry_preserves_schema_reasoning_and_token_limit() -> None:
    client = _client()
    client._client.chat.completions.create.side_effect = [
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
    first, second = (call.kwargs for call in client._client.chat.completions.create.call_args_list)
    assert first["response_format"] == second["response_format"] == _expected_response_format()
    assert first["reasoning_effort"] == second["reasoning_effort"] == "low"
    assert first["max_tokens"] == second["max_tokens"] == 48
    assert first["extra_body"] == {
        "prompt_cache_key": "cache-key",
        "reasoning": {"effort": "low"},
    }
    assert second["extra_body"] == {"reasoning": {"effort": "low"}}


def test_stream_cache_rejection_retry_preserves_schema_and_reasoning() -> None:
    client = _client()
    client._client.chat.completions.create.side_effect = [
        _ProviderError("prompt_cache_options unsupported"),
        _Stream([_chunk('{"value":"ok"}'), _chunk(finish_reason="stop")]),
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
    first, second = (call.kwargs for call in client._client.chat.completions.create.call_args_list)
    assert first["response_format"] == second["response_format"] == _expected_response_format()
    assert first["reasoning_effort"] == second["reasoning_effort"] == "low"
    assert first["max_tokens"] == second["max_tokens"] == 48
    assert "prompt_cache_key" in first["extra_body"]
    assert second["extra_body"] == {"reasoning": {"effort": "low"}}


@pytest.mark.parametrize(
    ("finish_reason", "refusal", "error_type"),
    [
        ("stop", "I cannot comply", LlmStructuredOutputRefusalError),
        ("length", None, LlmStructuredOutputTruncatedError),
        ("content_filter", None, LlmStructuredOutputInvalidResponseError),
        (None, None, LlmStructuredOutputInvalidResponseError),
    ],
)
def test_chat_rejects_non_complete_structured_responses(finish_reason, refusal, error_type) -> None:
    client = _client()
    client._client.chat.completions.create.return_value = _response(
        finish_reason=finish_reason,
        refusal=refusal,
    )

    with pytest.raises(error_type):
        client.chat(_messages(), max_tokens=32)

    assert client._active_requests == 0


def test_stream_rejects_invalid_json_after_complete_stop() -> None:
    client = _client()
    client._client.chat.completions.create.return_value = _Stream([_chunk('{"value":'), _chunk(finish_reason="stop")])

    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        client.chat_stream(_messages(), 32, lambda _text: None)


@pytest.mark.parametrize(
    ("finish_reason", "refusal", "error_type"),
    [
        ("stop", "I cannot comply", LlmStructuredOutputRefusalError),
        ("length", None, LlmStructuredOutputTruncatedError),
        ("content_filter", None, LlmStructuredOutputInvalidResponseError),
    ],
)
def test_stream_rejects_non_complete_structured_responses(finish_reason, refusal, error_type) -> None:
    client = _client()
    client._client.chat.completions.create.return_value = _Stream([
        _chunk('{"value":"ok"}'),
        _chunk(finish_reason=finish_reason, refusal=refusal),
    ])

    with pytest.raises(error_type):
        client.chat_stream(_messages(), 32, lambda _text: None)


def test_explicit_response_format_rejection_is_classified_as_unsupported() -> None:
    client = _client()
    client._client.chat.completions.create.side_effect = _ProviderError(
        "unknown parameter: response_format json_schema is unsupported"
    )

    with pytest.raises(LlmStructuredOutputUnsupportedError) as exc_info:
        client.chat(_messages(), max_tokens=32)

    assert exc_info.value.__cause__ is not None
