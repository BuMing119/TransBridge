"""Offline provider regressions for omitted and tool-only answer declarations."""

from dataclasses import replace

import pytest

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _requests, _until
from transbridge.infra.llm_tool_calling import LlmTurn
from transbridge.persistence.v2.ids import SessionId, SessionRef
from transbridge.smart_assistant.request_protocol import COVERAGE_TOOL, ROUTING_TOOL
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.message_history_view import visible_record

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _provider(environment, monkeypatch, respond):
    original = environment.client.chat_stream_with_tools
    execution = []

    def chat(messages, max_tokens, tools, on_chunk=None, **kwargs):
        if ROUTING_TOOL in {tool.name for tool in tools}:
            return original(messages, max_tokens, tools, on_chunk, **kwargs)
        execution.append((messages, tools))
        return respond(original, messages, max_tokens, tools, on_chunk, len(execution), kwargs)

    monkeypatch.setattr(environment.client, "chat_stream_with_tools", chat)
    return execution


@pytest.mark.parametrize("reason", ["stop", "end_turn"])
def test_plain_answer_gets_one_automatic_declaration_without_pausing(environment, monkeypatch, reason):
    def respond(original, messages, max_tokens, tools, on_chunk, attempt, kwargs):
        if attempt == 1:
            on_chunk("已有完整回答")
            return LlmTurn(text="已有完整回答", stop_reason=reason)
        assert any(message.get("content") == "已有完整回答" for message in messages)
        definition = next(tool for tool in tools if tool.name == COVERAGE_TOOL)
        assert "previous plain-text reply" in definition.description
        return original(messages, max_tokens, tools, on_chunk, **kwargs)

    execution = _provider(environment, monkeypatch, respond)
    environment.panel.chat.send_user_message("Explain tracked analysis")
    _until(lambda: bool(_requests(environment)) and _requests(environment)[0].terminal)
    _until(lambda: environment.binding.admission is None)
    request = _requests(environment)[0]
    assert len(execution) == 2
    assert request.status == "completed" and not request.pause_reasons
    assert len(request.evidence) == 1
    assert not environment.binding._user_stopped
    assert not any("ANSWER_INCOMPLETE" in b._text for b in environment.panel.chat.findChildren(MessageBubble))


@pytest.mark.parametrize("reason", ["stop", "length", "max_tokens", "error", "cancelled", None])
def test_unconfirmed_or_interrupted_text_never_counts_as_completed(environment, monkeypatch, reason):
    def respond(_original, _messages, _max_tokens, _tools, _on_chunk, _attempt, _kwargs):
        return LlmTurn(text="看起来像最终回答，但没有声明。", stop_reason=reason)

    execution = _provider(environment, monkeypatch, respond)
    environment.panel.chat.send_user_message("An incomplete tracked answer")
    _until(lambda: bool(_requests(environment)) and bool(_requests(environment)[0].pause_reasons))
    request = _requests(environment)[0]
    assert len(execution) == (2 if reason == "stop" else 1)
    assert not request.terminal and request.evidence == ()
    assert environment.binding.admission is None


def test_tool_only_answer_is_visible_saved_and_completed(environment, monkeypatch):
    def respond(original, messages, max_tokens, tools, _on_chunk, _attempt, kwargs):
        turn = original(messages, max_tokens, tools, lambda _: None, **kwargs)
        call = replace(turn.tool_calls[0], arguments={**turn.tool_calls[0].arguments, "answer": "参数中的完整回答"})
        return replace(turn, text="", tool_calls=(call,))

    execution = _provider(environment, monkeypatch, respond)
    environment.panel.chat.send_user_message("Answer through native tool arguments")
    _until(lambda: bool(_requests(environment)) and _requests(environment)[0].terminal)
    assert len(execution) == 1
    bubbles = environment.panel.chat.findChildren(MessageBubble)
    assert any(bubble._text == "参数中的完整回答" for bubble in bubbles)
    history = environment.panel.chat._conversation.get_transcript()
    answer = next(message for message in history if message.get("content") == "参数中的完整回答")
    assert _requests(environment)[0].evidence[0].reference == answer["message_id"]


def test_coverage_after_repair_can_block_instead_of_claiming_success(environment, monkeypatch):
    def respond(original, messages, max_tokens, tools, on_chunk, attempt, kwargs):
        if attempt == 1:
            return LlmTurn(text="还需要你补充目标语言。", stop_reason="stop")
        turn = original(messages, max_tokens, tools, lambda _: None, **kwargs)
        call = turn.tool_calls[0]
        arguments = {
            **call.arguments,
            "answer": "请补充目标语言。",
            "dispositions": {identity: "blocked" for identity in call.arguments["item_ids"]},
        }
        return replace(turn, text="", tool_calls=(replace(call, arguments=arguments),))

    execution = _provider(environment, monkeypatch, respond)
    environment.panel.chat.send_user_message("Answer needing clarification")
    _until(lambda: bool(_requests(environment)) and bool(_requests(environment)[0].items[0].waiting_reasons))
    assert len(execution) == 2
    request = _requests(environment)[0]
    assert not request.terminal and not request.evidence and not request.pause_reasons
    assert request.items[0].waiting_reasons == ("answer_blocked",)


@pytest.mark.parametrize("same_text", [True, False])
def test_repaired_answer_hides_only_exact_duplicate_in_live_and_restored_history(environment, monkeypatch, same_text):
    answer = "已说明全部分析结果。"
    final = answer if same_text else "补充：分析还发现一个兼容性限制。"

    def respond(original, messages, max_tokens, tools, on_chunk, attempt, kwargs):
        if attempt == 1:
            on_chunk(answer)
            return LlmTurn(text=answer, stop_reason="stop")
        turn = original(messages, max_tokens, tools, lambda _: None, **kwargs)
        call = replace(turn.tool_calls[0], arguments={**turn.tool_calls[0].arguments, "answer": final})
        return replace(turn, text="", tool_calls=(call,))

    _provider(environment, monkeypatch, respond)
    environment.panel.chat.send_user_message("Analysis completion")
    _until(lambda: bool(_requests(environment)) and _requests(environment)[0].terminal)
    live = [
        widget._text
        for widget in environment.panel.chat._message_list._owned_widgets
        if isinstance(widget, MessageBubble) and widget.role == "assistant"
    ]
    assert live == ([answer] if same_text else [answer, final])
    context = environment.binding.context
    snapshot = environment.services.session_lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    records = snapshot.backend_messages()
    restored = [visible for record in records if (visible := visible_record(record)) is not None]
    assert [r["content"] for r in restored if r["role"] == "assistant"] == live
    original = next(record for record in records if record.get("content") == answer)
    covered = next(
        record for record in records if any(c["name"] == COVERAGE_TOOL for c in record.get("tool_calls", ()))
    )
    assert covered.get("display_duplicate_of", "") == (original["message_id"] if same_text else "")
    assert _requests(environment)[0].evidence[0].reference == covered["message_id"]


def test_plain_text_cannot_repair_or_complete_execution_item(environment, monkeypatch):
    original = environment.client.chat_stream_with_tools
    executions = []

    def chat(messages, max_tokens, tools, on_chunk=None, **kwargs):
        if ROUTING_TOOL in {tool.name for tool in tools}:
            turn = original(messages, max_tokens, tools, on_chunk, **kwargs)
            call = turn.tool_calls[0]
            arguments = call.arguments
            arguments["directives"][0]["items"][0]["kind"] = "execution"
            return replace(turn, tool_calls=(replace(call, arguments=arguments),))
        executions.append(True)
        return LlmTurn(text="声称已经完成，但没有执行证据。", stop_reason="stop")

    monkeypatch.setattr(environment.client, "chat_stream_with_tools", chat)
    environment.panel.chat.send_user_message("Run an actual operation")
    _until(lambda: bool(_requests(environment)) and bool(_requests(environment)[0].pause_reasons))
    request = _requests(environment)[0]
    assert len(executions) == 1 and not request.evidence and not request.terminal


@pytest.mark.parametrize("action", ["stop", "hide", "switch_session"])
def test_pending_answer_repair_cannot_restart_after_user_interrupt(environment, monkeypatch, action):
    from PyQt6.QtTest import QTest

    executions = _provider(
        environment,
        monkeypatch,
        lambda *_args: LlmTurn(text="完成的回答", stop_reason="stop"),
    )
    recover = environment.binding.answers.recover

    def interrupt(turn):
        assert recover(turn)
        if action == "stop":
            environment.binding.management.stop_generation()
        elif action == "hide":
            environment.binding.set_active(False)
        else:
            # Session activation fences the old admission before loading new history.
            environment.binding.interrupt()
            environment.binding.context = replace(environment.binding.context, session_id="new-session")
        return True

    monkeypatch.setattr(environment.binding.answers, "recover", interrupt)
    environment.panel.chat.send_user_message("Stop before repair starts")
    _until(lambda: bool(executions) and environment.binding.admission is None)
    QTest.qWait(100)
    assert len(executions) == 1
