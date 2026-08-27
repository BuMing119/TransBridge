from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from transbridge.infra.llm_client import AnthropicClient
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


def _block(text: str):
    return SimpleNamespace(type="text", text=text)


def _response(
    content: list[object] | None = None,
    *,
    stop_reason: str | None = "end_turn",
):
    return SimpleNamespace(
        content=content if content is not None else [_block('{"value":"ok"}')],
        stop_reason=stop_reason,
    )


class _Stream:
    def __init__(self, chunks: list[str], *, stop_reason: str | None = "end_turn") -> None:
        self.text_stream = iter(chunks)
        self._final_message = SimpleNamespace(stop_reason=stop_reason)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def get_final_message(self):
        return self._final_message


def _client() -> AnthropicClient:
    client = AnthropicClient.__new__(AnthropicClient)
    client._api_key = "test"
    client._model = "test-model"
    client._max_retries = 0
    client._lock = threading.Lock()
    client._http_client = MagicMock()
    client._client = MagicMock()
    client._active_requests = 0
    return client


def _expected_output_config() -> dict:
    return {
        "format": {
            "type": "json_schema",
            "schema": _SCHEMA_JSON,
        }
    }


def test_chat_sends_native_schema_strips_metadata_and_joins_all_text_blocks() -> None:
    client = _client()
    client._client.messages.create.return_value = _response([
        _block('{"value":'),
        SimpleNamespace(type="thinking", thinking="..."),
        _block('"ok"}'),
    ])

    result = client.chat(_messages(), max_tokens=64)

    assert result == '{"value":"ok"}'
    kwargs = client._client.messages.create.call_args.kwargs
    assert kwargs == {
        "model": "test-model",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Translate this."}],
        "system": [{"type": "text", "text": "Return the result."}],
        "output_config": _expected_output_config(),
    }
    assert all(STRUCTURED_OUTPUT_METADATA_KEY not in message for message in kwargs["messages"])
    assert client._active_requests == 0


def test_plain_chat_keeps_legacy_first_content_block_behavior() -> None:
    client = _client()
    client._client.messages.create.return_value = SimpleNamespace(content=[_block("first"), _block("second")])

    assert client.chat([{"role": "user", "content": "hello"}], max_tokens=16) == "first"

    assert "output_config" not in client._client.messages.create.call_args.kwargs


def test_stream_sends_native_schema_uses_final_message_and_preserves_callbacks() -> None:
    client = _client()
    client._client.messages.stream.return_value = _Stream(['{"value":', '"ok"}'])
    received: list[str] = []

    result = client.chat_stream(_messages(), 32, received.append)

    assert result == '{"value":"ok"}'
    assert received == ['{"value":', '"ok"}']
    kwargs = client._client.messages.stream.call_args.kwargs
    assert kwargs["output_config"] == _expected_output_config()
    assert client._active_requests == 0


class _ProviderError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def test_cache_rejection_retry_preserves_output_config() -> None:
    client = _client()
    client._client.messages.create.side_effect = [
        _ProviderError("cache_control unsupported"),
        _response(),
    ]
    cached_blocks = [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}]
    plain_blocks = [{"type": "text", "text": "system"}]
    user_messages = [{"role": "user", "content": "Translate this."}]

    with patch(
        "transbridge.infra.prompt_cache.build_anthropic_system_blocks",
        side_effect=[(cached_blocks, user_messages), (plain_blocks, user_messages)],
    ):
        result = client.chat(_messages(), max_tokens=48)

    assert result == '{"value":"ok"}'
    first, second = (call.kwargs for call in client._client.messages.create.call_args_list)
    assert first["output_config"] == second["output_config"] == _expected_output_config()
    assert first["max_tokens"] == second["max_tokens"] == 48
    assert first["system"] == cached_blocks
    assert second["system"] == plain_blocks


def test_stream_cache_rejection_retry_preserves_output_config() -> None:
    client = _client()
    client._client.messages.stream.side_effect = [
        _ProviderError("cache_control unsupported"),
        _Stream(['{"value":"ok"}']),
    ]
    cached_blocks = [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}]
    plain_blocks = [{"type": "text", "text": "system"}]
    user_messages = [{"role": "user", "content": "Translate this."}]

    with patch(
        "transbridge.infra.prompt_cache.build_anthropic_system_blocks",
        side_effect=[(cached_blocks, user_messages), (plain_blocks, user_messages)],
    ):
        result = client.chat_stream(_messages(), max_tokens=48, chunk_callback=lambda _text: None)

    assert result == '{"value":"ok"}'
    first, second = (call.kwargs for call in client._client.messages.stream.call_args_list)
    assert first["output_config"] == second["output_config"] == _expected_output_config()
    assert first["max_tokens"] == second["max_tokens"] == 48
    assert first["system"] == cached_blocks
    assert second["system"] == plain_blocks


def test_system_block_compatibility_retry_preserves_output_config() -> None:
    client = _client()
    client._client.messages.create.side_effect = [
        TypeError("system must be a string"),
        _response(),
    ]

    assert client.chat(_messages(), max_tokens=32) == '{"value":"ok"}'

    first, second = (call.kwargs for call in client._client.messages.create.call_args_list)
    assert first["output_config"] == second["output_config"] == _expected_output_config()
    assert isinstance(first["system"], list)
    assert second["system"] == "Return the result."


@pytest.mark.parametrize(
    ("stop_reason", "error_type"),
    [
        ("refusal", LlmStructuredOutputRefusalError),
        ("max_tokens", LlmStructuredOutputTruncatedError),
        ("model_context_window_exceeded", LlmStructuredOutputTruncatedError),
        (None, LlmStructuredOutputInvalidResponseError),
        ("tool_use", LlmStructuredOutputInvalidResponseError),
    ],
)
def test_chat_rejects_non_complete_structured_responses(stop_reason, error_type) -> None:
    client = _client()
    client._client.messages.create.return_value = _response(stop_reason=stop_reason)

    with pytest.raises(error_type):
        client.chat(_messages(), max_tokens=32)

    assert client._active_requests == 0


def test_stream_rejects_invalid_json_after_complete_end_turn() -> None:
    client = _client()
    client._client.messages.stream.return_value = _Stream(['{"value":'])

    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        client.chat_stream(_messages(), 32, lambda _text: None)


@pytest.mark.parametrize(
    ("stop_reason", "error_type"),
    [
        ("refusal", LlmStructuredOutputRefusalError),
        ("max_tokens", LlmStructuredOutputTruncatedError),
        ("tool_use", LlmStructuredOutputInvalidResponseError),
    ],
)
def test_stream_rejects_non_complete_structured_responses(stop_reason, error_type) -> None:
    client = _client()
    client._client.messages.stream.return_value = _Stream(['{"value":"ok"}'], stop_reason=stop_reason)

    with pytest.raises(error_type):
        client.chat_stream(_messages(), 32, lambda _text: None)


def test_explicit_output_config_rejection_is_classified_as_unsupported() -> None:
    client = _client()
    client._client.messages.create.side_effect = _ProviderError(
        "unknown parameter: output_config json_schema is unsupported"
    )

    with pytest.raises(LlmStructuredOutputUnsupportedError) as exc_info:
        client.chat(_messages(), max_tokens=32)

    assert exc_info.value.__cause__ is not None
