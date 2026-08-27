from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from transbridge.infra.llm_tool_calling import (
    LlmToolDefinition,
    LlmToolProtocolError,
)
from transbridge.infra.openai_tool_calling import chat_stream_with_tools


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Stream:
    def __init__(self, events=(), *, enter_error: Exception | None = None):
        self._events = tuple(events)
        self._enter_error = enter_error

    def __enter__(self):
        if self._enter_error is not None:
            raise self._enter_error
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._events)


class _FailingStream(_Stream):
    def __init__(self, events, error: Exception):
        super().__init__(events)
        self._error = error

    def __iter__(self):
        yield from self._events
        raise self._error


class _CacheRejected(RuntimeError):
    status_code = 400


def _owner(*streams: _Stream):
    create = MagicMock(side_effect=streams)
    return SimpleNamespace(
        _model="gpt-test",
        _base_url="https://api.openai.com/v1",
        _lock=_Lock(),
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        _active_requests=0,
    )


def _choice(*, content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


def _tool_delta(index: int, *, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function" if call_id else None,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _tools() -> list[LlmToolDefinition]:
    return [
        LlmToolDefinition(
            name="alpha",
            description="Alpha tool",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
                "additionalProperties": False,
            },
            strict=True,
        ),
        LlmToolDefinition(
            name="beta",
            description="Beta tool",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
    ]


def test_streams_text_and_aggregates_interleaved_tool_calls_by_index() -> None:
    owner = _owner(
        _Stream([
            _choice(content="Checking "),
            _choice(tool_calls=[_tool_delta(1, call_id="call-b", name="beta", arguments='{"y":')]),
            _choice(tool_calls=[_tool_delta(0, call_id="call-a", name="alpha", arguments='{"x":')]),
            _choice(tool_calls=[_tool_delta(1, arguments="2}")]),
            _choice(tool_calls=[_tool_delta(0, arguments="1}")]),
            _choice(content="done", finish_reason="tool_calls"),
        ])
    )
    chunks: list[str] = []

    turn = chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 128, _tools(), chunks.append)

    assert turn.text == "Checking done"
    assert chunks == ["Checking ", "done"]
    assert [(call.id, call.name, call.arguments) for call in turn.tool_calls] == [
        ("call-a", "alpha", {"x": 1}),
        ("call-b", "beta", {"y": 2}),
    ]
    assert turn.stop_reason == "tool_calls"
    assert owner._active_requests == 0

    kwargs = owner._client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-test"
    assert kwargs["stream"] is True
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["max_tokens"] == 128
    assert kwargs["tools"][0] == {
        "type": "function",
        "function": {
            "name": "alpha",
            "description": "Alpha tool",
            "parameters": _tools()[0].input_schema,
            "strict": True,
        },
    }
    assert "strict" not in kwargs["tools"][1]["function"]


def test_plain_text_turn_accepts_stop_finish_reason() -> None:
    owner = _owner(_Stream([_choice(content="hello"), _choice(finish_reason="stop")]))

    turn = chat_stream_with_tools(owner, [{"role": "user", "content": "hi"}], 0, _tools(), lambda _text: None)

    assert turn.text == "hello"
    assert turn.tool_calls == ()
    assert turn.stop_reason == "stop"
    assert "max_tokens" not in owner._client.chat.completions.create.call_args.kwargs


def test_converts_canonical_assistant_calls_and_tool_results() -> None:
    owner = _owner(_Stream([_choice(content="done"), _choice(finish_reason="stop")]))
    messages = [
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-old", "name": "alpha", "arguments": {"x": 1}}],
            "provider_content": [{"type": "tool_use", "id": "ignored"}],
        },
        {
            "role": "tool",
            "tool_call_id": "call-old",
            "name": "alpha",
            "content": '{"success": true}',
            "display_summary": "ok",
            "is_error": False,
        },
    ]

    chat_stream_with_tools(owner, messages, 64, _tools(), lambda _text: None)

    sent = owner._client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-old",
                "type": "function",
                "function": {"name": "alpha", "arguments": '{"x": 1}'},
            }
        ],
    }
    assert sent[2] == {"role": "tool", "tool_call_id": "call-old", "content": '{"success": true}'}


@pytest.mark.parametrize(
    ("delta", "finish_reason", "message"),
    [
        (_tool_delta(0, name="alpha", arguments="{}"), "tool_calls", "without an id or name"),
        (_tool_delta(0, call_id="call-a", arguments="{}"), "tool_calls", "without an id or name"),
        (_tool_delta(0, call_id="call-a", name="alpha", arguments="{"), "tool_calls", "invalid JSON"),
        (_tool_delta(0, call_id="call-a", name="alpha", arguments="{}"), "stop", "finish_reason"),
        (_tool_delta(0, call_id="call-a", name="alpha", arguments="{}"), "length", "before the tool call"),
    ],
)
def test_rejects_incomplete_or_malformed_tool_calls(delta, finish_reason, message) -> None:
    owner = _owner(_Stream([_choice(tool_calls=[delta]), _choice(finish_reason=finish_reason)]))

    with pytest.raises(LlmToolProtocolError, match=message):
        chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 64, _tools(), lambda _text: None)

    assert owner._active_requests == 0


def test_cache_rejection_before_events_retries_clean_and_preserves_tools() -> None:
    error = _CacheRejected("prompt_cache_options unsupported")
    owner = _owner(
        _Stream(enter_error=error),
        _Stream([_choice(content="ok"), _choice(finish_reason="stop")]),
    )
    prepared = {
        "messages": [{"role": "system", "content": "cached"}],
        "request_options": {"prompt_cache_key": "cache-key"},
        "cache_mode": "automatic_prefix",
    }
    clean = [{"role": "system", "content": "clean"}, {"role": "user", "content": "run"}]

    with (
        patch("transbridge.infra.openai_tool_calling.prepare_openai_chat_cache_request", return_value=prepared),
        patch("transbridge.infra.openai_tool_calling.extract_prompt_cache_directives", return_value=(clean, ())),
    ):
        turn = chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 32, _tools(), lambda _text: None)

    assert turn.text == "ok"
    assert owner._client.chat.completions.create.call_count == 2
    first, second = [call.kwargs for call in owner._client.chat.completions.create.call_args_list]
    assert first["messages"] == prepared["messages"]
    assert first["extra_body"] == prepared["request_options"]
    assert second["messages"] == clean
    assert "extra_body" not in second
    assert second["tools"] == first["tools"]
    assert second["tool_choice"] == "auto"


def test_cache_rejection_after_any_stream_event_is_not_retried() -> None:
    error = _CacheRejected("cache_control rejected")
    owner = _owner(_FailingStream([_choice(content="partial")], error))
    chunks: list[str] = []

    with pytest.raises(_CacheRejected):
        chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 32, _tools(), chunks.append)

    assert chunks == ["partial"]
    assert owner._client.chat.completions.create.call_count == 1
    assert owner._active_requests == 0


def test_non_cache_request_error_is_not_retried() -> None:
    owner = _owner(_Stream(enter_error=RuntimeError("network unavailable")))

    with pytest.raises(RuntimeError, match="network unavailable"):
        chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 32, _tools(), lambda _text: None)

    assert owner._client.chat.completions.create.call_count == 1
    assert owner._active_requests == 0


def test_rejects_duplicate_tool_call_ids() -> None:
    owner = _owner(
        _Stream([
            _choice(tool_calls=[_tool_delta(0, call_id="dup", name="alpha", arguments="{}")]),
            _choice(tool_calls=[_tool_delta(1, call_id="dup", name="beta", arguments="{}")]),
            _choice(finish_reason="tool_calls"),
        ])
    )

    with pytest.raises(LlmToolProtocolError, match="duplicate"):
        chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 64, _tools(), lambda _text: None)


@pytest.mark.parametrize("finish_reason", [None, "tool_calls", "length"])
def test_rejects_incomplete_text_only_streams(finish_reason) -> None:
    events = [_choice(content="partial")]
    if finish_reason is not None:
        events.append(_choice(finish_reason=finish_reason))
    owner = _owner(_Stream(events))

    with pytest.raises(LlmToolProtocolError):
        chat_stream_with_tools(owner, [{"role": "user", "content": "run"}], 64, _tools(), lambda _text: None)
