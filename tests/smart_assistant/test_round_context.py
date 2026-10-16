"""Workspace changes append decision material without rewriting rules or evidence."""

from dataclasses import replace
from types import SimpleNamespace

from transbridge.application.assistant_context.models import CompactionSummary, ContextEpoch, state_material
from transbridge.application.assistant_context.projection import append_context
from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.smart_assistant.prompts import build_system_prompt
from transbridge.smart_assistant.round_context import refresh_round_context, with_workspace_snapshot


def test_each_round_reflects_project_and_progress_without_changing_static_system():
    ctx = SimpleNamespace(collection=[SimpleNamespace(translation="", context="NPC_:FULL")], esp_path="First.esp")
    conversation = ConversationManager()
    conversation.add_user("Explain the selected entry", message_id="input")
    request = UserRequest(
        "request", "session", "Explain", (RequestItem("answer", "Explain"),), source_message_ids=("input",)
    )
    selection = {"entry_ids": ["original"], "project": "First"}
    first_snapshot = refresh_round_context(conversation, ctx)
    first = append_context(
        conversation.get_transcript(),
        request,
        with_workspace_snapshot({"selection": selection}, first_snapshot),
        config_digest="fixed",
    )
    original_system = conversation.get_transcript()[0]
    ctx.esp_path = "Second.esp"
    ctx.collection[0].translation = "名称"
    second_snapshot = refresh_round_context(conversation, ctx)
    second = append_context(
        conversation.get_transcript(),
        request,
        with_workspace_snapshot({"selection": selection}, second_snapshot),
        config_digest="fixed",
        previous=first,
    )
    assert "Plugin: First" in first_snapshot and "0 translated" in first_snapshot
    assert "Plugin: Second" in second_snapshot and "1 translated" in second_snapshot
    assert conversation.get_transcript()[0] == original_system
    assert "Plugin: First" not in second.systems_json and "Plugin: Second" not in second.systems_json
    assert second.epoch_id == first.epoch_id and second.source_digests == first.source_digests
    assert second.messages[: len(first.messages)] == first.messages
    latest = max((state_material(i) for i in second.items if i.kind == "state"), key=lambda pair: pair[0])[1]
    assert latest == {"selection": selection, "workspace_snapshot": second_snapshot}
    summary = CompactionSummary("summary", "Prior immutable summary", ("input",))
    compacted = replace(second, summaries=(summary,), items=tuple(i for i in second.items if i.kind == "state"))
    restored = ContextEpoch.from_dict(compacted.to_dict())
    ctx.collection = None
    next_epoch = append_context(
        conversation.get_transcript(),
        request,
        with_workspace_snapshot({"selection": selection}, refresh_round_context(conversation, ctx)),
        config_digest="fixed",
        previous=restored,
    )
    assert next_epoch.summaries == (summary,)
    assert next_epoch.messages[: len(restored.messages)] == restored.messages
    assert "No collection is loaded" in next_epoch.items[-1].message["content"]


def test_generated_workspace_migrates_without_removing_static_or_manual_rules():
    conversation = ConversationManager()
    conversation.add_system(build_system_prompt("Plugin: OLD_WORKSPACE") + "\nCustom rule: preserve identifiers.")
    before = conversation.get_transcript()[0]
    refresh_round_context(conversation, SimpleNamespace(collection=None))
    after = conversation.get_transcript()[0]
    assert "OLD_WORKSPACE" not in after["content"]
    assert "Custom rule: preserve identifiers." in after["content"]
    assert "Native tool calls" in after["content"]
    assert after["message_id"] != before["message_id"]
    refresh_round_context(conversation, SimpleNamespace(collection=None))
    assert conversation.get_transcript()[0] == after
    conversation.add_system("Explicit manual system instructions")
    refresh_round_context(conversation, SimpleNamespace(collection=None))
    assert conversation.get_history()[0]["content"] == "Explicit manual system instructions"


def test_legacy_round_receives_fresh_workspace_without_persisting_it(monkeypatch):
    from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator

    monkeypatch.setattr("transbridge.smart_assistant.conversation_orchestrator.QTimer.singleShot", lambda *_: None)
    conversation = ConversationManager()
    conversation.add_user("Explain", message_id="input")
    ctx = SimpleNamespace(collection=[SimpleNamespace(translation="", context="NPC_:FULL")], esp_path="First.esp")
    orchestrator = SimpleNamespace(
        _shutdown_complete=False,
        cancel_current_round=lambda: None,
        _get_llm_client=lambda: object(),
        _generation=0,
        _react_depth=0,
        _conversation=conversation,
        _ctx=ctx,
        _on_thinking_indicator_hide=lambda: None,
        _cached_llm_config=LLMConfig(model="example", api_key="test"),
    )
    ConversationOrchestrator.start_round(orchestrator)
    first = orchestrator._round_messages
    assert "Plugin: First" in first[-1]["content"]
    assert '"material_only":true' in first[-1]["content"]
    evidence = conversation.get_transcript()
    ctx.esp_path = "Second.esp"
    ctx.collection[0].translation = "名称"
    ConversationOrchestrator.start_round(orchestrator)
    assert "Plugin: Second" in orchestrator._round_messages[-1]["content"]
    assert "1 translated" in orchestrator._round_messages[-1]["content"]
    assert "Plugin: First" not in str(orchestrator._round_messages)
    assert conversation.get_transcript() == evidence
    assert len(evidence) == 2
