"""Exercise the installed SDK's actual wire serialization without network access."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from transbridge.infra import llm_client
from transbridge.infra.llm_reasoning import (
    ReasoningCapability,
    ReasoningCapabilityKey,
    ReasoningIntent,
    ReasoningScopedLLMClient,
)
from transbridge.infra.llm_structured_outputs import LlmOutputSchema, attach_structured_output_directive
from transbridge.infra.prompt_cache import attach_prompt_cache_directive


@pytest.fixture
def wire(monkeypatch, request):
    requests = []
    clients = []
    reject_effort = getattr(request, "param", {}).get("reject_effort", False)

    def respond(request):
        requests.append(request)
        payload = json.loads(request.content)
        if reject_effort and len(requests) == 1:
            return httpx.Response(
                400,
                json={
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": "output_config.effort is not supported"},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_offline",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                "content": [{"type": "text", "text": '{"value":"translated"}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 25, "output_tokens": 6},
            },
        )

    def make_http_client(**kwargs):
        client = httpx.Client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(llm_client, "httpx", SimpleNamespace(**{**vars(httpx), "Client": make_http_client}))
    yield requests
    for client in clients:
        client.close()


def _config(base_url):
    return SimpleNamespace(
        provider="anthropic",
        api_key="offline-test-key",
        model="claude-sonnet-5-5",
        base_url=base_url,
        llm_max_retries=0,
    )


def test_factory_uses_configured_gateway_and_keeps_schema_effort_and_auto_limit(wire, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://environment.invalid/ignored")
    schema = LlmOutputSchema(
        "translation",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    system = attach_structured_output_directive({"role": "system", "content": "Translate faithfully."}, schema)
    system = attach_prompt_cache_directive(
        system, cache_key="offline-stage", profile="single_stable_prefix", breakpoint="FINAL"
    )
    messages = [system, {"role": "user", "content": "Synthetic input"}]
    client = llm_client.create_llm_client(_config("https://configured.invalid/anthropic"))
    # The protocol supplies this capability; the fixture deliberately performs no discovery network calls.
    patch = client.build_reasoning_patch(
        ReasoningCapability.supported_controls(low="anthropic_effort_low"), ReasoningIntent.PREFER_LOW
    )
    assert patch is not None
    assert client.chat_with_reasoning(messages, 0, patch) == '{"value":"translated"}'
    request = wire[0]
    assert str(request.url) == "https://configured.invalid/anthropic/v1/messages"
    payload = json.loads(request.content)
    assert payload["max_tokens"] == 8192
    assert payload["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": schema.schema}}
    assert payload["system"] == [{"type": "text", "text": "Translate faithfully."}]
    assert payload["messages"] == [{"role": "user", "content": "Synthetic input"}]
    assert "_transbridge" not in request.content.decode()
    assert "cache_control" not in request.content.decode()
    assert messages[0] is system  # Conversion must leave the application request untouched.


def test_cancel_rebuild_keeps_resolved_environment_address(wire, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://first.invalid/claude")
    client = llm_client.create_llm_client(_config(""))
    client.chat([{"role": "user", "content": "First"}], 32)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://second.invalid/claude")
    client.cancel()
    client.chat([{"role": "user", "content": "Second"}], 32)
    assert [request.url.host for request in wire] == ["first.invalid", "first.invalid"]
    assert [json.loads(request.content)["max_tokens"] for request in wire] == [32, 32]


def test_legacy_anthropic_openai_address_is_rejected_before_any_request(wire):
    with pytest.raises(ValueError, match="OpenAI"):
        llm_client.create_llm_client(_config("https://api.openai.com/v1"))
    assert wire == []


@pytest.mark.parametrize("wire", [{"reject_effort": True}], indirect=True)
def test_real_sdk_effort_rejection_retries_with_schema_intact(wire):
    config = _config("https://configured.invalid")
    client = llm_client.create_llm_client(config)
    capability = ReasoningCapability.supported_controls(low="anthropic_effort_low")
    invalidated = []
    manager = SimpleNamespace(
        schedule=lambda *_args: None,
        resolve=lambda *_args, **_kwargs: capability,
        mark_indeterminate=invalidated.append,
    )
    scoped = ReasoningScopedLLMClient(
        client, ReasoningCapabilityKey.from_config(config), ReasoningIntent.PREFER_LOW, manager=manager
    )
    schema = LlmOutputSchema(
        "translation",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    messages = [
        attach_structured_output_directive({"role": "system", "content": "Return the result."}, schema),
        {"role": "user", "content": "Synthetic input"},
    ]
    assert scoped.chat(messages, 0) == '{"value":"translated"}'
    assert len(wire) == 2
    first, retry = (json.loads(request.content) for request in wire)
    assert first["output_config"]["effort"] == "low"
    assert retry["output_config"] == {"format": first["output_config"]["format"]}
    assert first["messages"] == retry["messages"]
    assert first["max_tokens"] == retry["max_tokens"] == 8192
    assert len(invalidated) == 1
