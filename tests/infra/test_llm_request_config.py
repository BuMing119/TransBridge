from unittest.mock import MagicMock

import pytest

from transbridge.infra.llm_client import OpenAICompatibleClient
from transbridge.infra.llm_request_config import (
    ANTHROPIC_DEFAULT_ENDPOINT,
    OPENAI_DEFAULT_ENDPOINT,
    is_official_anthropic_endpoint,
    resolve_anthropic_endpoint,
)


def test_anthropic_endpoint_precedence_preserves_custom_path(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert resolve_anthropic_endpoint() == ANTHROPIC_DEFAULT_ENDPOINT
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example/claude")
    assert resolve_anthropic_endpoint(" ") == "https://env.example/claude"
    assert resolve_anthropic_endpoint(" https://explicit.example/gateway/ ") == "https://explicit.example/gateway/"


def test_stale_openai_address_is_rejected_before_environment_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example/claude")
    with pytest.raises(ValueError, match="指向 OpenAI.*清空"):
        resolve_anthropic_endpoint(OPENAI_DEFAULT_ENDPOINT)


@pytest.mark.parametrize("suffix", ["?credential=secret-123", "?", "#secret-123", "#"])
def test_anthropic_sdk_base_url_errors_omit_unusable_query_and_fragment(suffix):
    with pytest.raises(ValueError, match="查询参数和片段") as exc:
        resolve_anthropic_endpoint(f"https://proxy.example/claude{suffix}")
    assert "secret-123" not in str(exc.value)
    assert "proxy.example" not in str(exc.value)


@pytest.mark.parametrize(
    ("endpoint", "official"),
    [
        ("https://api.anthropic.com/", True),
        ("https://API.ANTHROPIC.COM:443", True),
        ("http://api.anthropic.com/", False),
        ("https://api.anthropic.com/claude", False),
        ("https://api.anthropic.com/?x=1", False),
        ("https://user:pass@api.anthropic.com/", False),
        ("https://api.anthropic.com.proxy.example/", False),
    ],
)
def test_anthropic_capability_detection_requires_the_official_endpoint(endpoint, official):
    assert is_official_anthropic_endpoint(endpoint) is official


@pytest.mark.parametrize("invalid_limit", [-1, False, True])
def test_openai_rejects_invalid_limits_on_all_client_entry_points(monkeypatch, invalid_limit):
    sdk = MagicMock()
    monkeypatch.setattr(OpenAICompatibleClient, "_make_client", lambda _self: sdk)
    client = OpenAICompatibleClient("test", OPENAI_DEFAULT_ENDPOINT, "test-model")
    messages = [{"role": "user", "content": "Hello"}]
    received = []
    try:
        with pytest.raises(ValueError, match="max_tokens"):
            client.chat(messages, invalid_limit)
        with pytest.raises(ValueError, match="max_tokens"):
            client.chat_stream(messages, invalid_limit, received.append)
        with pytest.raises(ValueError, match="max_tokens"):
            client.chat_stream_with_tools(messages, invalid_limit, [], received.append)
        assert not sdk.mock_calls
    finally:
        client._http_client.close()
