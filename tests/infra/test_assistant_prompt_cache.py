from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from transbridge.infra import anthropic_tool_calling, openai_tool_calling
from transbridge.infra.assistant_prompt_cache import (
    anthropic_assistant_cache_options,
    decorate_assistant_messages,
)
from transbridge.infra.prompt_cache import (
    PROMPT_CACHE_METADATA_KEY,
    build_anthropic_system_blocks,
    prepare_openai_chat_cache_request,
    validate_prompt_cache_directives,
)


def _history():
    return [
        {"role": "system", "content": "Fixed rules: never write translations."},
        {"role": "user", "content": "S1: first immutable segment"},
        {"role": "user", "content": "S2: second immutable segment"},
        {"role": "user", "content": "S3: third immutable segment"},
        {"role": "user", "content": "State revision 4; inspect entry A."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "name": "lookup", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Synthetic result"},
    ]


def _openai(messages, *, model="gpt-4.1", base_url="https://api.openai.com/v1"):
    owner = SimpleNamespace(_model=model, _base_url=base_url)
    return openai_tool_calling._request_kwargs(owner, messages, 128, [])


def _anthropic(messages):
    system, body = build_anthropic_system_blocks(messages, model="claude-sonnet-4-6")
    return system, anthropic_tool_calling._convert_messages(body, merge_following_user=False)


def test_append_preserves_openai_payload_prefix_and_all_summary_segments():
    history = _history()
    original = deepcopy(history)
    before = _openai(decorate_assistant_messages(history, namespace="config/session/request"))
    after = _openai(
        decorate_assistant_messages(
            history + [{"role": "user", "content": "Continue"}], namespace="config/session/request"
        )
    )
    assert after["messages"][: len(before["messages"])] == before["messages"]
    assert after["extra_body"] == before["extra_body"]
    assert [m["content"] for m in before["messages"][1:4]] == [m["content"] for m in history[1:4]]
    assert history == original
    assert all(not any(key.startswith("_transbridge") for key in m) for m in after["messages"])


def test_append_after_tool_result_does_not_merge_new_user_into_old_anthropic_block():
    history = _history()
    before = _anthropic(decorate_assistant_messages(history, namespace="stable"))
    after = _anthropic(
        decorate_assistant_messages(history + [{"role": "user", "content": "Continue"}], namespace="stable")
    )
    assert before[0] == after[0]
    assert after[1][: len(before[1])] == before[1]
    assert before[1][-1]["content"] == [{"type": "tool_result", "tool_use_id": "call-1", "content": "Synthetic result"}]


def test_unknown_endpoints_and_models_get_no_guessed_cache_fields():
    messages = decorate_assistant_messages(_history(), namespace="stable")
    assert "extra_body" not in _openai(messages, base_url="https://proxy.invalid/v1")
    assert "extra_body" not in _openai(messages, model="unknown-model")
    assert anthropic_assistant_cache_options(messages, model="unknown", base_url="https://api.anthropic.com") == {}
    assert (
        anthropic_assistant_cache_options(messages, model="claude-sonnet-4-6", base_url="https://proxy.invalid") == {}
    )


def test_known_anthropic_uses_top_level_automatic_history_cache():
    messages = decorate_assistant_messages(_history(), namespace="stable")
    options = anthropic_assistant_cache_options(
        messages, model="claude-sonnet-4-6", base_url="https://api.anthropic.com/"
    )
    assert options == {"cache_control": {"type": "ephemeral"}}
    system, body = _anthropic(messages)
    assert "cache_control" not in str(system + body)


def test_disabling_cache_removes_explicit_markers_without_changing_history_grouping():
    messages = decorate_assistant_messages(_history(), namespace="stable")
    disabled = decorate_assistant_messages(messages, namespace="stable", enabled=False)
    assert not any(PROMPT_CACHE_METADATA_KEY in m for m in disabled)
    assert _anthropic(messages) == _anthropic(disabled)
    assert "extra_body" not in _openai(disabled)
    assert (
        anthropic_assistant_cache_options(disabled, model="claude-sonnet-4-6", base_url="https://api.anthropic.com")
        == {}
    )


def test_translation_profile_does_not_accept_assistant_history():
    messages = decorate_assistant_messages(_history(), namespace="stable")
    messages[0][PROMPT_CACHE_METADATA_KEY]["profile"] = "single_stable_prefix"
    _, directives = validate_prompt_cache_directives(messages, "single_stable_prefix")
    assert directives == ()


def test_late_system_message_disables_assistant_cache():
    messages = decorate_assistant_messages(
        _history() + [{"role": "system", "content": "mutable state"}], namespace="stable"
    )
    request = prepare_openai_chat_cache_request(
        model="gpt-4.1", base_url="https://api.openai.com/v1", messages=messages
    )
    assert request["request_options"] == {}


def test_explicit_openai_rule_breakpoint_stays_fixed_on_append(monkeypatch):
    monkeypatch.setattr("transbridge.infra.assistant_prompt_cache.estimate_prompt_tokens", lambda *_: 1200)
    before = _openai(decorate_assistant_messages(_history(), namespace="stable"), model="gpt-5.6")
    after = _openai(
        decorate_assistant_messages(_history() + [{"role": "user", "content": "Next"}], namespace="stable"),
        model="gpt-5.6",
    )
    assert before["messages"] == after["messages"][:-1]
    assert before["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}


def test_provider_content_replay_preserves_signed_blocks():
    messages = _history()
    blocks = [
        {"type": "thinking", "thinking": "synthetic", "signature": "synthetic-signature"},
        {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {}},
    ]
    messages[-2]["provider_content"] = blocks
    _, wire = _anthropic(decorate_assistant_messages(messages, namespace="stable"))
    assert wire[-2]["content"] == blocks


def test_anthropic_top_level_cache_rejection_retries_once_with_same_layout():
    class Rejected(RuntimeError):
        status_code = 400

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __iter__(self):
            return iter(())

        def get_final_message(self):
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}

    stream = MagicMock(side_effect=[Rejected("cache_control unsupported"), Stream()])
    owner = SimpleNamespace(
        _lock=threading.Lock(),
        _active_requests=0,
        _model="claude-sonnet-4-6",
        _client=SimpleNamespace(messages=SimpleNamespace(stream=stream), base_url="https://api.anthropic.com"),
    )
    records = []
    anthropic_tool_calling.chat_stream_with_tools(
        owner,
        decorate_assistant_messages(_history(), namespace="stable"),
        128,
        [],
        lambda _: None,
        usage_callback=records.append,
    )
    first, second = [call.kwargs for call in stream.call_args_list]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in second
    assert first["messages"] == second["messages"]
    assert first["system"] == second["system"]
    assert records[1].retry_of == records[0].attempt_id
