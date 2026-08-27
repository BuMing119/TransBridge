from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn
from transbridge.smart_assistant.conversation_orchestrator import (
    ConversationOrchestrator,
    _smart_assistant_max_tokens,
)

_APP = QApplication.instance() or QApplication([])


def test_anthropic_assistant_uses_positive_default_max_tokens() -> None:
    config = type("Config", (), {"provider": "anthropic", "max_output_tokens": 0})()
    assert _smart_assistant_max_tokens(config) == 4096


def test_configured_max_tokens_is_preserved() -> None:
    config = type("Config", (), {"provider": "anthropic", "max_output_tokens": 900})()
    assert _smart_assistant_max_tokens(config) == 900


class _Conversation:
    def __init__(self) -> None:
        self.assistant_messages: list[str] = []
        self.assistant_turns: list[LlmTurn] = []

    def get_messages(self) -> list[dict]:
        return [{"role": "system", "content": "ready"}]

    def add_assistant(self, text: str) -> None:
        self.assistant_messages.append(text)

    def add_assistant_turn(self, turn: LlmTurn) -> None:
        self.assistant_turns.append(turn)

    def close_pending_tool_calls(self, _reason: str) -> int:
        return 0


class _Worker:
    instances: list[_Worker] = []

    def __init__(self, client, messages, max_tokens=None, tools=None) -> None:
        self.on_chunk = None
        self.on_finished = None
        self.on_error = None
        self.on_token_usage = None
        self.alive = False
        self.cancelled = False
        self.join_timeout = None
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def cancel(self) -> None:
        self.cancelled = True

    def join(self, timeout=None) -> None:
        self.join_timeout = timeout
        self.alive = False


def _orchestrator(monkeypatch):
    _Worker.instances.clear()
    monkeypatch.setattr("transbridge.smart_assistant.chat_worker.ChatWorker", _Worker)
    conversation = _Conversation()
    parsed: list[dict] = []
    chunks: list[str] = []
    systems: list[str] = []
    value = ConversationOrchestrator(
        ctx=object(),
        conversation_manager=conversation,
        tool_execution_handler=object(),
        on_system_message=systems.append,
        on_streaming_flush=lambda text, bubble, dirty: chunks.append(text),
        on_response_parsed=parsed.append,
    )
    value._get_llm_client = lambda: object()
    return value, conversation, parsed, chunks, systems


def test_cancel_discards_worker_callback_already_queued_to_gui(monkeypatch) -> None:
    value, conversation, parsed, chunks, systems = _orchestrator(monkeypatch)
    value._generation = 10
    value._active_generation = 10
    value._round_messages = [{"role": "user", "content": "hello"}]
    value._stage_c(10)
    worker = _Worker.instances[-1]
    late_finished = worker.on_finished
    late_chunk = worker.on_chunk
    late_error = worker.on_error

    def emit_late_callbacks() -> None:
        late_chunk("late chunk")
        late_error("late error")
        late_finished("late response")

    emitter = threading.Thread(target=emit_late_callbacks)
    emitter.start()
    emitter.join()
    value.cancel_current_round()
    _APP.processEvents()

    assert conversation.assistant_messages == []
    assert parsed == []
    assert chunks == []
    assert systems == []
    value.shutdown(wait=True, timeout=0.1)


def test_shutdown_waits_for_real_worker_and_late_callback_is_inert(monkeypatch) -> None:
    value, conversation, parsed, _, systems = _orchestrator(monkeypatch)
    value._generation = 3
    value._active_generation = 3
    value._round_messages = [{"role": "user", "content": "hello"}]
    value._stage_c(3)
    worker = _Worker.instances[-1]
    late_error = worker.on_error

    stopped = value.shutdown(wait=True, timeout=0.25)
    emitter = threading.Thread(target=lambda: late_error("late error"))
    emitter.start()
    emitter.join()
    _APP.processEvents()

    assert stopped is True
    assert worker.cancelled is True
    assert worker.join_timeout is not None
    assert conversation.assistant_messages == []
    assert parsed == []
    assert systems == []


def test_stale_generation_cannot_mutate_current_stream(monkeypatch) -> None:
    value, _, _, chunks, _ = _orchestrator(monkeypatch)
    value._generation = 1
    value._active_generation = 1
    value._round_messages = []
    value._stage_c(1)
    old_worker = _Worker.instances[-1]

    value.cancel_current_round()
    value._generation = 2
    value._active_generation = 2
    value._round_messages = []
    value._stage_c(2)
    new_worker = _Worker.instances[-1]
    value._streaming_generation = 2

    value._on_chunk(1, old_worker, "old")
    value._on_chunk(2, new_worker, "new")

    assert value._streaming_text == "new"
    assert chunks == []
    value.shutdown(wait=True, timeout=0.1)


def test_native_tool_turn_is_normalized_with_call_id(monkeypatch) -> None:
    value, conversation, parsed, _, _ = _orchestrator(monkeypatch)
    value._generation = 1
    value._active_generation = 1
    value._round_messages = []
    value._stage_c(1)
    worker = _Worker.instances[-1]

    value._on_finished(
        1,
        worker,
        LlmTurn(tool_calls=(LlmToolCall("call-1", "get_statistics", {}),), stop_reason="tool_calls"),
    )

    assert conversation.assistant_turns[0].tool_calls[0].id == "call-1"
    assert parsed[0]["steps"][0]["tool_call_id"] == "call-1"
    value.shutdown(wait=True, timeout=0.1)


def test_json_looking_text_is_never_executed(monkeypatch) -> None:
    value, _, parsed, _, _ = _orchestrator(monkeypatch)
    value._generation = 1
    value._active_generation = 1
    value._round_messages = []
    value._stage_c(1)
    worker = _Worker.instances[-1]

    value._on_finished(
        1,
        worker,
        LlmTurn(text='{"mode":"react","steps":[{"tool":"get_statistics","args":{}}]}'),
    )

    assert parsed[0]["steps"] == []
    value.shutdown(wait=True, timeout=0.1)
