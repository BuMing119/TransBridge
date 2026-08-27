from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from transbridge.infra import anthropic_tool_calling as adapter
from transbridge.infra.llm_tool_calling import LlmToolDefinition, LlmToolProtocolError


class _Stream:
    def __init__(self, events, final_message):
        self._events = events
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final_message


def _block(block_type: str, **values):
    return SimpleNamespace(type=block_type, **values)


def _owner(stream_side_effect):
    messages_api = MagicMock()
    messages_api.stream.side_effect = stream_side_effect
    return SimpleNamespace(
        _lock=threading.Lock(),
        _client=SimpleNamespace(messages=messages_api),
        _active_requests=0,
        _model="claude-sonnet-4-6",
    )


def _tool(*, strict: bool = True):
    return LlmToolDefinition(
        name="lookup",
        description="Look up one entry",
        input_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
        strict=strict,
    )


def test_maps_history_tools_stream_events_and_complete_message():
    events = [
        _block("content_block_delta", delta=_block("text_delta", text="正在")),
        _block("content_block_delta", delta=_block("input_json_delta", partial_json='{"key":')),
        _block("content_block_delta", delta=_block("text_delta", text="查询")),
    ]
    final = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            _block("thinking", thinking="summary", signature="signed"),
            _block("text", text="正在查询"),
            _block("tool_use", id="toolu_1", name="lookup", input={"key": "A"}),
        ],
    )
    owner = _owner([_Stream(events, final)])
    chunks: list[str] = []
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "find A and B"},
        {
            "role": "assistant",
            "content": "calling",
            "tool_calls": [
                {"id": "old_1", "name": "lookup", "arguments": {"key": "A"}},
                {"id": "old_2", "name": "lookup", "arguments": {"key": "B"}},
            ],
        },
        {"role": "tool", "tool_call_id": "old_1", "content": "result A"},
        {"role": "tool", "tool_call_id": "old_2", "content": "failed", "is_error": True},
        {"role": "user", "content": "continue"},
    ]

    turn = adapter.chat_stream_with_tools(owner, messages, 512, [_tool()], chunks.append)

    assert chunks == ["正在", "查询"]
    assert turn.text == "正在查询"
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].id == "toolu_1"
    assert turn.tool_calls[0].arguments == {"key": "A"}
    assert [block["type"] for block in turn.provider_content] == ["thinking", "text", "tool_use"]
    assert turn.provider_content[0]["signature"] == "signed"
    assert owner._active_requests == 0

    kwargs = owner._client.messages.stream.call_args.kwargs
    assert kwargs["system"] == [{"type": "text", "text": "system"}]
    assert kwargs["tools"] == [
        {
            "name": "lookup",
            "description": "Look up one entry",
            "input_schema": _tool().input_schema,
            "strict": True,
        }
    ]
    assistant = kwargs["messages"][1]
    assert [block["type"] for block in assistant["content"]] == ["text", "tool_use", "tool_use"]
    result_message = kwargs["messages"][2]
    assert [block["type"] for block in result_message["content"]] == ["tool_result", "tool_result", "text"]
    assert result_message["content"][1]["is_error"] is True


def test_replays_provider_content_without_duplicating_text_or_tool_calls():
    provider_content = [
        {"type": "thinking", "thinking": "summary", "signature": "sig"},
        {"type": "tool_use", "id": "toolu_7", "name": "lookup", "input": {"key": "x"}},
    ]
    final = SimpleNamespace(stop_reason="end_turn", content=[_block("text", text="done")])
    owner = _owner([_Stream([], final)])

    adapter.chat_stream_with_tools(
        owner,
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "must not duplicate",
                "tool_calls": [{"id": "toolu_7", "name": "lookup", "arguments": {"key": "x"}}],
                "provider_content": provider_content,
            },
            {"role": "tool", "tool_call_id": "toolu_7", "content": "ok"},
        ],
        256,
        [_tool(strict=False)],
        lambda _text: None,
    )

    kwargs = owner._client.messages.stream.call_args.kwargs
    assert kwargs["messages"][1]["content"] == provider_content
    assert "strict" not in kwargs["tools"][0]


def test_rejects_tool_call_cut_off_by_max_tokens():
    final = SimpleNamespace(
        stop_reason="max_tokens",
        content=[_block("tool_use", id="toolu_cut", name="lookup", input={"key": "A"})],
    )
    owner = _owner([_Stream([], final)])

    with pytest.raises(LlmToolProtocolError, match="before the tool call was complete"):
        adapter.chat_stream_with_tools(
            owner,
            [{"role": "user", "content": "lookup"}],
            16,
            [_tool()],
            lambda _text: None,
        )
    assert owner._active_requests == 0


def test_rejects_tool_use_with_unexpected_stop_reason():
    final = SimpleNamespace(
        stop_reason="end_turn",
        content=[_block("tool_use", id="toolu_bad", name="lookup", input={"key": "A"})],
    )
    owner = _owner([_Stream([], final)])

    with pytest.raises(LlmToolProtocolError, match="unexpected stop_reason"):
        adapter.chat_stream_with_tools(
            owner,
            [{"role": "user", "content": "lookup"}],
            16,
            [_tool()],
            lambda _text: None,
        )


def test_old_sdk_system_blocks_fall_back_to_text(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "build_anthropic_system_blocks",
        lambda *_args, **_kwargs: (
            [{"type": "text", "text": "stable"}],
            [{"role": "user", "content": "hello"}],
        ),
    )
    final = SimpleNamespace(stop_reason="end_turn", content=[_block("text", text="ok")])
    owner = _owner([TypeError("system must be a string"), _Stream([], final)])

    turn = adapter.chat_stream_with_tools(
        owner,
        [{"role": "system", "content": "stable"}, {"role": "user", "content": "hello"}],
        128,
        [_tool(strict=False)],
        lambda _text: None,
    )

    assert turn.text == "ok"
    first, second = owner._client.messages.stream.call_args_list
    assert isinstance(first.kwargs["system"], list)
    assert second.kwargs["system"] == "stable"


def test_cache_rejection_retry_preserves_tools_and_uses_no_cache_messages(monkeypatch):
    calls: list[bool] = []

    def fake_build(messages, *, model, enable_cache=True):
        del messages, model
        calls.append(enable_cache)
        system = [{"type": "text", "text": "stable"}]
        if enable_cache:
            system[0]["cache_control"] = {"type": "ephemeral"}
        return system, [{"role": "user", "content": "hello"}]

    monkeypatch.setattr(adapter, "build_anthropic_system_blocks", fake_build)

    class CacheRejected(Exception):
        status_code = 400
        message = "cache_control is unsupported"

    final = SimpleNamespace(stop_reason="end_turn", content=[_block("text", text="ok")])
    owner = _owner([CacheRejected(), _Stream([], final)])

    turn = adapter.chat_stream_with_tools(
        owner,
        [{"role": "system", "content": "stable"}, {"role": "user", "content": "hello"}],
        128,
        [_tool()],
        lambda _text: None,
    )

    assert turn.text == "ok"
    assert calls == [True, False]
    first, second = owner._client.messages.stream.call_args_list
    assert first.kwargs["tools"] == second.kwargs["tools"]
    assert "cache_control" in first.kwargs["system"][0]
    assert "cache_control" not in second.kwargs["system"][0]


def test_cache_rejection_after_text_is_not_retried(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "build_anthropic_system_blocks",
        lambda *_args, **_kwargs: (
            [{"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}}],
            [{"role": "user", "content": "hello"}],
        ),
    )

    class PartialStream(_Stream):
        def __iter__(self):
            yield _block("content_block_delta", delta=_block("text_delta", text="partial"))
            exc = RuntimeError("cache_control failed after output")
            exc.status_code = 400
            exc.message = "cache_control failed after output"
            raise exc

    owner = _owner([PartialStream([], None)])
    chunks: list[str] = []

    with pytest.raises(RuntimeError, match="after output"):
        adapter.chat_stream_with_tools(
            owner,
            [{"role": "user", "content": "hello"}],
            128,
            [_tool()],
            chunks.append,
        )
    assert chunks == ["partial"]
    assert owner._client.messages.stream.call_count == 1


def test_tool_event_prevents_cache_retry(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "build_anthropic_system_blocks",
        lambda *_args, **_kwargs: (
            [{"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}}],
            [{"role": "user", "content": "hello"}],
        ),
    )

    class PartialToolStream(_Stream):
        def __iter__(self):
            yield _block("content_block_delta", delta=_block("input_json_delta", partial_json="{"))
            exc = RuntimeError("cache_control failed after tool delta")
            exc.status_code = 400
            exc.message = "cache_control failed after tool delta"
            raise exc

    owner = _owner([PartialToolStream([], None)])
    with pytest.raises(RuntimeError, match="after tool delta"):
        adapter.chat_stream_with_tools(
            owner,
            [{"role": "user", "content": "hello"}],
            128,
            [_tool()],
            lambda _text: None,
        )
    assert owner._client.messages.stream.call_count == 1


def test_rejects_duplicate_tool_call_ids():
    final = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            _block("tool_use", id="dup", name="lookup", input={"key": "A"}),
            _block("tool_use", id="dup", name="lookup", input={"key": "B"}),
        ],
    )
    owner = _owner([_Stream([], final)])

    with pytest.raises(LlmToolProtocolError, match="duplicate"):
        adapter.chat_stream_with_tools(
            owner,
            [{"role": "user", "content": "lookup"}],
            128,
            [_tool()],
            lambda _text: None,
        )


@pytest.mark.parametrize("stop_reason", [None, "tool_use", "max_tokens"])
def test_rejects_incomplete_text_only_messages(stop_reason):
    final = SimpleNamespace(stop_reason=stop_reason, content=[_block("text", text="partial")])
    owner = _owner([_Stream([], final)])

    with pytest.raises(LlmToolProtocolError):
        adapter.chat_stream_with_tools(
            owner,
            [{"role": "user", "content": "hello"}],
            128,
            [_tool()],
            lambda _text: None,
        )
