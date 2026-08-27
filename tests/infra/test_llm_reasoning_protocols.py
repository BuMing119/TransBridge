from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from transbridge.infra.llm_client import AnthropicClient, OpenAICompatibleClient
from transbridge.infra.llm_reasoning import ReasoningCapability, ReasoningIntent
from transbridge.infra.llm_reasoning_protocols import (
    anthropic_reasoning_capability,
    anthropic_reasoning_patch,
    detect_openai_reasoning_capability,
    openai_reasoning_patch,
)
from transbridge.infra.prompt_cache import attach_prompt_cache_directive, build_prompt_cache_key


class _ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _response(*, reasoning_tokens: int = 0, reasoning_content: str = ""):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="OK", reasoning_content=reasoning_content),
            )
        ],
        usage=SimpleNamespace(
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        ),
    )


def test_openai_probe_requires_invalid_value_validation_before_support() -> None:
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("reasoning_effort") == "__transbridge_probe__":
            raise _ProviderError("reasoning_effort must be one of none, low, medium")
        return _response()

    capability = detect_openai_reasoning_capability(create, model="reasoner")

    assert capability.direct_mechanism == "reasoning_effort"
    assert capability.low_mechanism == "reasoning_effort"
    assert [call.get("reasoning_effort") for call in calls] == ["__transbridge_probe__", "none", "low"]


def test_openai_probe_skips_silently_ignored_field_and_finds_thinking_protocol() -> None:
    def create(**kwargs):
        extra = kwargs.get("extra_body", {})
        thinking = extra.get("thinking", {})
        if thinking.get("type") == "__transbridge_probe__":
            raise _ProviderError("thinking.type invalid; allowed values are enabled and disabled")
        return _response()

    capability = detect_openai_reasoning_capability(create, model="reasoner")

    assert capability.direct_mechanism == "thinking_disabled"


def test_openai_probe_does_not_claim_support_when_all_invalid_fields_are_ignored() -> None:
    capability = detect_openai_reasoning_capability(lambda **_kwargs: _response(), model="reasoner")

    assert capability.status.value == "indeterminate"


def test_openai_probe_rejects_valid_disable_when_reasoning_evidence_remains() -> None:
    def create(**kwargs):
        if kwargs.get("reasoning_effort") == "__transbridge_probe__":
            raise _ProviderError("invalid reasoning_effort enum")
        if kwargs.get("reasoning_effort") == "none":
            return _response(reasoning_tokens=12)
        if kwargs.get("reasoning_effort") == "low":
            raise _ProviderError("reasoning_effort low is unsupported")
        extra = kwargs.get("extra_body", {})
        field = "thinking" if "thinking" in extra else "enable_thinking"
        raise _ProviderError(f"unknown parameter: {field}")

    capability = detect_openai_reasoning_capability(create, model="reasoner")

    assert capability.status.value == "unsupported"


def test_openai_probe_can_record_low_when_direct_disable_is_not_supported() -> None:
    def create(**kwargs):
        effort = kwargs.get("reasoning_effort")
        if effort == "__transbridge_probe__":
            raise _ProviderError("invalid reasoning_effort enum")
        if effort == "none":
            raise _ProviderError("reasoning_effort must be low, medium, or high")
        if effort == "low":
            return _response(reasoning_tokens=4)
        extra = kwargs.get("extra_body", {})
        field = "thinking" if "thinking" in extra else "enable_thinking"
        raise _ProviderError(f"unknown parameter: {field}")

    capability = detect_openai_reasoning_capability(create, model="reasoner")

    assert capability.direct_mechanism == ""
    assert capability.low_mechanism == "reasoning_effort"
    assert openai_reasoning_patch(capability, ReasoningIntent.PREFER_DIRECT) is None
    assert openai_reasoning_patch(capability, ReasoningIntent.PREFER_LOW).standard == {"reasoning_effort": "low"}


def test_protocol_patches_degrade_low_to_direct_when_no_low_contract_exists() -> None:
    effort = ReasoningCapability.supported("reasoning_effort")
    thinking = ReasoningCapability.supported("thinking_disabled")

    assert openai_reasoning_patch(effort, ReasoningIntent.PREFER_LOW).standard == {"reasoning_effort": "low"}
    assert openai_reasoning_patch(thinking, ReasoningIntent.PREFER_LOW).extra_body == {
        "thinking": {"type": "disabled"}
    }
    anthropic = anthropic_reasoning_capability()
    assert anthropic_reasoning_patch(anthropic, ReasoningIntent.PREFER_DIRECT).extra_body == {}


def _openai_client() -> OpenAICompatibleClient:
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._api_key = "secret"
    client._base_url = "https://api.openai.com/v1"
    client._model = "reasoner"
    client._max_retries = 2
    client._lock = MagicMock()
    client._lock.__enter__.return_value = None
    client._lock.__exit__.return_value = False
    client._http_client = MagicMock()
    client._client = MagicMock()
    client._active_requests = 0
    return client


def test_prompt_cache_rejection_retry_preserves_reasoning_control() -> None:
    client = _openai_client()
    system = "Stable translation instructions. " * 200
    key = build_prompt_cache_key("test.v1", system)
    messages = [
        attach_prompt_cache_directive(
            {"role": "system", "content": system},
            cache_key=key,
            profile="single_stable_prefix",
            breakpoint="FINAL",
        ),
        {"role": "user", "content": "Hello"},
    ]
    patch = openai_reasoning_patch(
        ReasoningCapability.supported("thinking_disabled"),
        ReasoningIntent.PREFER_DIRECT,
    )
    call_count = 0

    def create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _ProviderError("prompt_cache not supported")
        return _response()

    client._client.chat.completions.create.side_effect = create

    assert client.chat_with_reasoning(messages, 32, patch) == "OK"
    retry = client._client.chat.completions.create.call_args_list[1].kwargs
    assert retry["extra_body"] == {"thinking": {"type": "disabled"}}


def test_stream_prompt_cache_retry_preserves_reasoning_control() -> None:
    client = _openai_client()
    system = "Stable translation instructions. " * 200
    key = build_prompt_cache_key("test.v1", system)
    messages = [
        attach_prompt_cache_directive(
            {"role": "system", "content": system},
            cache_key=key,
            profile="single_stable_prefix",
            breakpoint="FINAL",
        ),
        {"role": "user", "content": "Hello"},
    ]
    patch = openai_reasoning_patch(
        ReasoningCapability.supported("thinking_disabled"),
        ReasoningIntent.PREFER_DIRECT,
    )
    call_count = 0

    def create(**_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _ProviderError("prompt_cache not supported")
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        stream.__iter__.return_value = iter(
            [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"))])]
        )
        return stream

    client._client.chat.completions.create.side_effect = create
    chunks: list[str] = []

    assert client.chat_stream_with_reasoning(messages, 32, chunks.append, patch) == "OK"
    retry = client._client.chat.completions.create.call_args_list[1].kwargs
    assert retry["extra_body"] == {"thinking": {"type": "disabled"}}
    assert chunks == ["OK"]


def test_anthropic_controlled_call_is_protocol_omission() -> None:
    client = AnthropicClient.__new__(AnthropicClient)
    client.chat = MagicMock(return_value="OK")

    patch = anthropic_reasoning_patch(anthropic_reasoning_capability(), ReasoningIntent.PREFER_DIRECT)

    assert client.chat_with_reasoning([], 32, patch) == "OK"
    client.chat.assert_called_once_with([], 32)
