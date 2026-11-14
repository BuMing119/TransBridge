from __future__ import annotations

from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from transbridge.infra.llm_client import AnthropicClient
from transbridge.infra.llm_reasoning import (
    ReasoningCapability,
    ReasoningCapabilityKey,
    ReasoningCapabilityManager,
    ReasoningCapabilityStatus,
    ReasoningCapabilityStore,
    ReasoningIntent,
    ReasoningScopedLLMClient,
)
from transbridge.infra.llm_reasoning_protocols import anthropic_reasoning_patch, detect_anthropic_reasoning_capability
from transbridge.infra.llm_structured_outputs import (
    LlmOutputSchema,
    LlmStructuredOutputUnsupportedError,
    attach_structured_output_directive,
)

_OFFICIAL = "https://api.anthropic.com"
_GATEWAY = "https://gateway.example.test/anthropic"
_DISABLED = "anthropic_thinking_disabled"
_BETWEEN_TOOLS = "anthropic_thinking_between_tools"
_LOW = "anthropic_effort_low"
_SENTINEL = "__transbridge_probe__"
_SCHEMA = {"type": "object", "additionalProperties": False}
_FORMAT = {"type": "json_schema", "schema": _SCHEMA}
_OUTPUT_SCHEMA = LlmOutputSchema("test_result", _SCHEMA)
_USER = {"role": "user", "content": "Translate this."}
_MESSAGES = [
    attach_structured_output_directive({"role": "system", "content": "Return a JSON object."}, _OUTPUT_SCHEMA),
    _USER,
]


class _ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _client(base_url: str = _OFFICIAL) -> AnthropicClient:
    client = AnthropicClient.__new__(AnthropicClient)
    client._api_key = "test"
    client._base_url = base_url
    client._model = "example-model"
    client._max_retries = 0
    client._lock = threading.Lock()
    client._client = MagicMock(base_url=base_url)
    client._active_requests = 0
    return client


def _text_response(text: str = "{}"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
    )


def _stream(*chunks: str):
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.text_stream = iter(chunks)
    stream.get_final_message.return_value = SimpleNamespace(stop_reason="end_turn")
    return stream


def _capabilities(*, disabled: bool, between_tools: bool, low: bool) -> dict:
    return {
        "thinking": {
            "types": {
                "disabled": {"supported": disabled},
                "between_tools": {"supported": between_tools},
            },
        },
        "effort": {"low": {"supported": low}},
    }


@pytest.mark.parametrize(
    ("disabled", "between_tools", "low", "direct_mechanism"),
    [(True, True, True, _DISABLED), (False, True, False, _BETWEEN_TOOLS), (False, False, False, "")],
)
def test_official_anthropic_uses_independent_model_capabilities(
    disabled: bool, between_tools: bool, low: bool, direct_mechanism: str
) -> None:
    create = MagicMock()
    capability = detect_anthropic_reasoning_capability(
        create,
        model="example-model",
        retrieve_model=lambda _model: {
            "capabilities": _capabilities(disabled=disabled, between_tools=between_tools, low=low)
        },
    )

    create.assert_not_called()
    assert capability.direct_mechanism == direct_mechanism
    assert capability.low_mechanism == (_LOW if low else "")
    expected_status = ReasoningCapabilityStatus.SUPPORTED if direct_mechanism else ReasoningCapabilityStatus.UNSUPPORTED
    assert capability.status is expected_status
    assert anthropic_reasoning_patch(capability, ReasoningIntent.INHERIT) is None
    if direct_mechanism:
        direct = anthropic_reasoning_patch(capability, ReasoningIntent.PREFER_DIRECT)
        expected_type = "disabled" if disabled else "between_tools"
        assert direct.standard == {"thinking": {"type": expected_type}}
        preferred_low = anthropic_reasoning_patch(capability, ReasoningIntent.PREFER_LOW)
        assert preferred_low.standard == ({"output_config": {"effort": "low"}} if low else direct.standard)
    else:
        assert anthropic_reasoning_patch(capability, ReasoningIntent.PREFER_DIRECT) is None


def test_locked_sdk_preserves_official_model_metadata_and_low_only_does_not_disable() -> None:
    requests: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "type": "model",
                "id": "example-model",
                "created_at": "2026-01-01T00:00:00Z",
                "display_name": "Example Model",
                "capabilities": _capabilities(disabled=False, between_tools=False, low=True),
            },
        )

    client = _client()
    transport = httpx.MockTransport(handle)
    with anthropic.Anthropic(api_key="test", base_url=_OFFICIAL, http_client=httpx.Client(transport=transport)) as sdk:
        client._client = sdk
        capability = client.detect_reasoning_capability()

    assert requests == [("GET", "/v1/models/example-model")]
    assert capability.status is ReasoningCapabilityStatus.SUPPORTED
    assert anthropic_reasoning_patch(capability, ReasoningIntent.PREFER_DIRECT) is None
    assert anthropic_reasoning_patch(capability, ReasoningIntent.PREFER_LOW).standard == {
        "output_config": {"effort": "low"},
    }
    assert client._active_requests == 0


def test_gateway_ignoring_both_invalid_values_has_no_proven_control() -> None:
    client = _client(_GATEWAY)
    client._client.models.retrieve.return_value = {
        "capabilities": _capabilities(disabled=True, between_tools=True, low=True)
    }
    client._client.messages.create.return_value = _text_response()

    capability = client.detect_reasoning_capability()

    assert capability.status is ReasoningCapabilityStatus.INDETERMINATE
    client._client.models.retrieve.assert_not_called()
    calls = [call.kwargs for call in client._client.messages.create.call_args_list]
    assert [call.get("thinking") for call in calls] == [{"type": _SENTINEL}, None]
    assert [call.get("output_config") for call in calls] == [None, {"effort": _SENTINEL}]
    assert all(call["messages"] == [{"role": "user", "content": "Reply with OK."}] for call in calls)


def test_missing_official_capabilities_fall_back_to_confirming_legal_values() -> None:
    create = MagicMock(
        side_effect=[
            _ProviderError("thinking.type: Input should be enabled, disabled, or between_tools"),
            _ProviderError("thinking.type.disabled is not supported"),
            _text_response(),
            _ProviderError("output_config.effort must be one of low, medium, high"),
            _text_response(),
        ]
    )

    capability = detect_anthropic_reasoning_capability(
        create, model="example-model", retrieve_model=lambda _model: {"id": "example-model"}
    )

    calls = [call.kwargs for call in create.call_args_list]
    assert [call["thinking"]["type"] for call in calls if "thinking" in call] == [
        _SENTINEL,
        "disabled",
        "between_tools",
    ]
    assert [call["output_config"]["effort"] for call in calls if "output_config" in call] == [_SENTINEL, "low"]
    assert capability.direct_mechanism == _BETWEEN_TOOLS
    assert capability.low_mechanism == _LOW


def test_thinking_content_disproves_direct_but_does_not_disprove_low() -> None:
    reasoning_response = SimpleNamespace(content=[SimpleNamespace(type="redacted_thinking")])
    create = MagicMock(
        side_effect=[
            _ProviderError("thinking.type is invalid"),
            reasoning_response,
            reasoning_response,
            _ProviderError("output_config.effort is invalid"),
            reasoning_response,
        ]
    )

    capability = detect_anthropic_reasoning_capability(create, model="example-model")

    assert capability.status is ReasoningCapabilityStatus.SUPPORTED
    assert capability.direct_mechanism == ""
    assert capability.low_mechanism == _LOW


def test_malformed_success_response_does_not_prove_controls_work() -> None:
    malformed = {"type": "error"}
    create = MagicMock(
        side_effect=[
            _ProviderError("thinking.type is invalid"),
            malformed,
            malformed,
            _ProviderError("output_config.effort is invalid"),
            malformed,
        ]
    )

    capability = detect_anthropic_reasoning_capability(create, model="example-model")

    assert capability.status is ReasoningCapabilityStatus.INDETERMINATE


def test_authentication_error_stops_anthropic_probe_without_logging_body(caplog) -> None:
    create = MagicMock(side_effect=_ProviderError("private-provider-body", 401))

    with caplog.at_level("DEBUG"):
        capability = detect_anthropic_reasoning_capability(create, model="example-model")

    assert capability.status is ReasoningCapabilityStatus.INDETERMINATE
    assert create.call_count == 1
    assert "401" in caplog.text
    assert "private-provider-body" not in caplog.text


def test_anthropic_plain_chat_uses_disabled_and_skips_thinking_blocks() -> None:
    client = _client()
    response = _text_response("first")
    response.content.insert(0, SimpleNamespace(type="thinking", thinking="private"))
    response.content.append(SimpleNamespace(type="text", text="second"))
    client._client.messages.create.return_value = response
    patch = anthropic_reasoning_patch(
        ReasoningCapability.supported_controls(direct=_DISABLED), ReasoningIntent.PREFER_DIRECT
    )

    assert client.chat_with_reasoning([_USER], 32, patch) == "first"
    assert client._client.messages.create.call_args.kwargs["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("stream", [False, True])
def test_anthropic_cache_retry_preserves_schema_and_low_effort(monkeypatch, stream: bool) -> None:
    client = _client()
    cached_blocks = [{"type": "text", "text": "System", "cache_control": {"type": "ephemeral"}}]
    plain_blocks = [{"type": "text", "text": "System"}]
    build_blocks = MagicMock(side_effect=[(cached_blocks, [_USER]), (plain_blocks, [_USER])])
    monkeypatch.setattr("transbridge.infra.prompt_cache.build_anthropic_system_blocks", build_blocks)
    request = client._client.messages.stream if stream else client._client.messages.create
    request.side_effect = [
        _ProviderError("cache_control is not supported"),
        _stream("{}") if stream else _text_response(),
    ]
    patch = anthropic_reasoning_patch(ReasoningCapability.supported_controls(low=_LOW), ReasoningIntent.PREFER_LOW)
    chunks: list[str] = []

    if stream:
        assert client.chat_stream_with_reasoning(_MESSAGES, 32, chunks.append, patch) == "{}"
        assert chunks == ["{}"]
    else:
        assert client.chat_with_reasoning(_MESSAGES, 32, patch) == "{}"

    first, retry = (call.kwargs for call in request.call_args_list)
    assert first["output_config"] == retry["output_config"] == {"format": _FORMAT, "effort": "low"}
    assert first["system"] == cached_blocks
    assert retry["system"] == plain_blocks


@pytest.mark.parametrize("stream", [False, True])
def test_anthropic_rejecting_low_effort_retries_with_native_schema(tmp_path: Path, stream: bool) -> None:
    client = _client()
    request = client._client.messages.stream if stream else client._client.messages.create
    request.side_effect = [
        _ProviderError("output_config.effort 'low' is not supported"),
        _stream("{}") if stream else _text_response(),
    ]
    store = ReasoningCapabilityStore(tmp_path / "capabilities.json")
    key = ReasoningCapabilityKey("anthropic", _OFFICIAL, client._model)
    store.put(key, ReasoningCapability.supported_controls(low=_LOW))
    scoped = ReasoningScopedLLMClient(
        client, key, ReasoningIntent.PREFER_LOW, manager=ReasoningCapabilityManager(store)
    )
    chunks: list[str] = []

    if stream:
        assert scoped.chat_stream(_MESSAGES, 32, chunks.append) == "{}"
        assert chunks == ["{}"]
    else:
        assert scoped.chat(_MESSAGES, 32) == "{}"

    first, retry = (call.kwargs for call in request.call_args_list)
    assert first["output_config"] == {"format": _FORMAT, "effort": "low"}
    assert retry["output_config"] == {"format": _FORMAT}
    assert store.get(key).status is ReasoningCapabilityStatus.INDETERMINATE


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_ProviderError("output_config.format json_schema is not supported"), LlmStructuredOutputUnsupportedError),
        (_ProviderError("unknown parameter: output_config"), LlmStructuredOutputUnsupportedError),
        (_ProviderError("model is not available", 404), _ProviderError),
        (_ProviderError("output_config.effort low is currently rate limited", 429), _ProviderError),
    ],
)
def test_anthropic_non_reasoning_errors_do_not_retry_or_invalidate(tmp_path: Path, error, expected) -> None:
    client = _client()
    client._client.messages.create.side_effect = error
    store = ReasoningCapabilityStore(tmp_path / "capabilities.json")
    key = ReasoningCapabilityKey("anthropic", _OFFICIAL, client._model)
    capability = ReasoningCapability.supported_controls(low=_LOW)
    store.put(key, capability)
    scoped = ReasoningScopedLLMClient(
        client, key, ReasoningIntent.PREFER_LOW, manager=ReasoningCapabilityManager(store)
    )

    with pytest.raises(expected):
        scoped.chat(_MESSAGES, 32)

    assert client._client.messages.create.call_count == 1
    assert store.get(key) == capability


@pytest.mark.parametrize(
    ("model", "capability", "intent", "error", "with_schema", "expected"),
    [
        (
            "thinking-v2",
            ReasoningCapability.supported_controls(direct=_DISABLED),
            ReasoningIntent.PREFER_DIRECT,
            {"message": "Invalid model: thinking-v2"},
            False,
            anthropic.BadRequestError,
        ),
        (
            "low-effort-v2",
            ReasoningCapability.supported_controls(low=_LOW),
            ReasoningIntent.PREFER_LOW,
            {"message": "The low-effort-v2 model is not allowed for this account"},
            False,
            anthropic.BadRequestError,
        ),
        (
            "example-model",
            ReasoningCapability.supported_controls(low=_LOW),
            ReasoningIntent.PREFER_LOW,
            {"param": "model", "message": "Model unavailable (example: output_config.effort is not supported)"},
            False,
            anthropic.BadRequestError,
        ),
        (
            "example-model",
            ReasoningCapability.supported_controls(low=_LOW),
            ReasoningIntent.PREFER_LOW,
            {
                "param": "output_config.format",
                "message": "output_config.format is not supported (accepted example: output_config.effort)",
            },
            True,
            LlmStructuredOutputUnsupportedError,
        ),
    ],
)
def test_sdk_error_field_never_confuses_model_or_schema_for_reasoning(
    tmp_path: Path, model, capability, intent, error, with_schema, expected
) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", **error}})

    client = _client(_GATEWAY)
    client._model = model
    key = ReasoningCapabilityKey("anthropic", _GATEWAY, model)
    store = ReasoningCapabilityStore(tmp_path / "capabilities.json")
    store.put(key, capability)
    scoped = ReasoningScopedLLMClient(client, key, intent, manager=ReasoningCapabilityManager(store))
    transport = httpx.MockTransport(handle)

    with anthropic.Anthropic(api_key="test", base_url=_GATEWAY, http_client=httpx.Client(transport=transport)) as sdk:
        client._client = sdk
        with pytest.raises(expected):
            scoped.chat(_MESSAGES if with_schema else [_USER], 32)

    assert requests == ["/anthropic/v1/messages"]
    assert store.get(key) == capability
    assert client._active_requests == 0
