"""Restoring/interrupting a view cannot replace application-owned tool results."""

import pytest

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn
from transbridge.smart_assistant.conversation_manager import ConversationManager


def test_reserved_call_survives_abort_and_restore_until_durable_receipt_arrives():
    conversation = ConversationManager()
    conversation.add_assistant_turn(LlmTurn(tool_calls=(LlmToolCall("query", "read_request_state", {}),)))
    conversation.reserve_control_call("query", "turn")
    assert conversation.close_pending_tool_calls() == 0
    restored = ConversationManager()
    restored.from_dict(conversation.to_dict())
    assert not any(message["role"] == "tool" for message in restored.get_history())
    receipt = {
        "message_id": "saved",
        "role": "tool",
        "tool_call_id": "query",
        "name": "read_request_state",
        "content": '{"text": "saved page"}',
        "display_summary": "",
        "is_error": False,
    }
    restored.apply_control_receipt(receipt)
    restored.apply_control_receipt(receipt)
    assert restored.get_transcript()[-1] == receipt
    assert len([m for m in restored.get_history() if m["role"] == "tool"]) == 1
    with pytest.raises(ValueError, match="conflicting"):
        restored.apply_control_receipt({**receipt, "message_id": "wrong", "is_error": True})


def test_same_session_reload_merges_late_commit_before_unsaved_local_input():
    conversation = ConversationManager()
    conversation.add_user("original", message_id="input")
    old_load = conversation.to_dict()
    conversation.add_assistant_turn(LlmTurn(tool_calls=(LlmToolCall("query", "read_request_state", {}),)))
    conversation.reserve_control_call("query", "turn")
    receipt = {
        "message_id": "receipt",
        "role": "tool",
        "tool_call_id": "query",
        "name": "read_request_state",
        "content": '{"text": "saved"}',
        "display_summary": "",
        "is_error": False,
    }
    conversation.apply_control_receipt(receipt)
    saved = conversation.get_transcript()
    conversation.from_dict(old_load)
    conversation.add_user("new local input", message_id="local")
    conversation.merge_saved_records(saved)
    assert [m["message_id"] for m in conversation.get_transcript()] == [m["message_id"] for m in saved] + ["local"]
    conversation.apply_control_receipt(receipt)
    assert len([m for m in conversation.get_history() if m.get("tool_call_id") == "query"]) == 1
