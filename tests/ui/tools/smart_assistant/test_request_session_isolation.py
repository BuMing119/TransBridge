"""Real Qt admission and clear boundaries against isolated Session persistence."""

import json
from threading import Event

from PyQt6.QtWidgets import QPushButton
import pytest

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _seed_long_request, _until
from transbridge.persistence.v2.ids import SessionId, SessionRef

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _idle(binding):
    _until(lambda: not binding._accepting)


def _switch_session(environment, session_id):
    panel, binding = environment.panel, environment.binding
    panel._on_switch_session(session_id)
    _until(
        lambda: (
            panel._workspace.ready()
            and panel._current_session_id() == session_id
            and binding.context.session_id == session_id
            and not binding._accepting
        )
    )


@pytest.mark.parametrize("switch", [False, True])
def test_failed_admission_preserves_new_draft_and_current_execution_state(environment, monkeypatch, switch):
    panel, binding = environment.panel, environment.binding
    chat = panel.chat
    binding.set_active(False)
    original = binding.context.session_id
    entered, proceed = Event(), Event()

    def reject(*args, **kwargs):
        entered.set()
        assert proceed.wait(8)
        raise RuntimeError("isolated admission failure")

    monkeypatch.setattr(environment.service, "accept_input", reject)
    try:
        assert chat.send_user_message("original input")
        _until(entered.is_set)
        if switch:
            panel._on_create_session("second")
            _until(lambda: binding.context.session_id != original)
        chat.set_input("new draft")
    finally:
        proceed.set()
    _idle(binding)
    assert chat._input.toPlainText() == "new draft"
    assert not binding._user_stopped
    assert binding.inputs.failures[original][0].text == "original input"
    if switch:
        _switch_session(environment, original)
        chat.set_input("new draft")
    restore = next(
        widget
        for widget in chat._message_list._owned_widgets
        if isinstance(widget, QPushButton) and widget.text() == "恢复未发送内容"
    )
    restore.click()
    assert chat._input.toPlainText() == "new draft\noriginal input"
    assert not binding.inputs.failures[original]


def test_stop_belongs_to_its_original_session(environment):
    binding, panel = environment.binding, environment.panel
    original = binding.context.session_id
    binding.management.stop_generation()
    assert binding._user_stopped
    panel._on_create_session("independent")
    _until(lambda: binding.context.session_id != original)
    _idle(binding)
    assert not binding._user_stopped
    request = _seed_long_request(environment)
    binding.wake()
    _until(
        lambda: any(
            r.request_id == request.request_id and r.terminal
            for r in environment.service.requests(environment.service.state(binding.context))
        )
    )
    assert environment.client.calls
    _switch_session(environment, original)
    assert binding._user_stopped


@pytest.mark.parametrize("restore_session", [False, True])
def test_retry_unsaved_input_keeps_identity_material_and_skill(environment, monkeypatch, restore_session):
    binding, chat, service = environment.binding, environment.panel.chat, environment.service
    binding.set_active(False)
    original = service.accept_input
    calls = []

    def fail_after_saved(*args, **kwargs):
        calls.append(kwargs)
        accepted = original(*args, **kwargs)
        if len(calls) == 1:
            raise OSError("delivery lost after commit")
        return accepted

    monkeypatch.setattr(service, "accept_input", fail_after_saved)
    skill = {"name": "check", "prompt": "original skill"}
    assert binding.submit("original", skill_context=skill)
    _idle(binding)
    if restore_session:
        session_id = binding.context.session_id
        environment.panel._on_create_session("temporary")
        _idle(binding)
        _switch_session(environment, session_id)
    wakes = []
    monkeypatch.setattr(binding, "wake", lambda *_args: wakes.append(True))
    chat.set_input("unrelated new draft")
    skill["prompt"] = "changed elsewhere"
    retry = next(
        widget
        for widget in chat._message_list._owned_widgets
        if isinstance(widget, QPushButton) and widget.text() == "重试提交"
    )
    retry.click()
    _idle(binding)
    assert chat._input.toPlainText() == "unrelated new draft"
    assert len(calls) == 2 and calls[0]["command_id"] == calls[1]["command_id"]
    assert calls[1]["skill_context"]["prompt"] == "original skill"
    assert len(service.state(binding.context)["ingress"]) == 1
    assert not binding.inputs.failures[binding.context.session_id]
    assert wakes
    assert sum(m.get("content") == "original" for m in chat._conversation.get_transcript()) == 1
    assert sum(w.property("message_id") is not None for w in chat._message_list._owned_widgets) == 1


def test_clear_survives_reopen_and_stale_save_without_deleting_evidence(environment):
    panel, binding, service = environment.panel, environment.binding, environment.service
    chat = panel.chat
    binding.set_active(False)
    chat._conversation.add_user("original message", message_id="original")
    assert panel._persist_authoritative_chat()
    original_context = binding.context
    stale = chat._conversation.get_transcript()
    chat._clear_conversation()
    _until(lambda: not binding.inputs.clearing)
    assert chat._conversation.get_transcript() == []
    service.save_history(original_context, stale)
    snapshot = service.lifecycle.read_session(SessionRef(SessionId(original_context.session_id)), original_context)
    assert any(m.get("message_id") == "original" for m in snapshot.backend_messages())
    assert not snapshot.visible_messages()
    panel._on_create_session("second")
    _idle(binding)
    _switch_session(environment, original_context.session_id)
    assert not chat._conversation.get_transcript()
    assert not chat._message_list._owned_widgets
    assert chat.send_user_message("new after clear")
    _idle(binding)
    assert [m["content"] for m in chat._conversation.get_transcript()] == ["new after clear"]
    assert [m["content"] for m in chat._message_list._history_snapshot()] == ["new after clear"]


def test_clear_storage_failure_retains_original_conversation(environment, monkeypatch):
    binding, chat = environment.binding, environment.panel.chat
    binding.set_active(False)
    chat._conversation.add_user("keep on failure", message_id="keep")
    chat.load_history(chat._conversation.get_transcript())

    def reject(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("transbridge.ui.tools.smart_assistant.conversation_clear.clear_conversation", reject)
    chat._clear_conversation()
    _until(lambda: not binding.inputs.clearing)
    assert any(m.get("message_id") == "keep" for m in chat._conversation.get_transcript())
    assert any(w.property("message_id") == "keep" for w in chat._message_list._owned_widgets)
    assert any("disk unavailable" in getattr(w, "_text", "") for w in chat._message_list._owned_widgets)


def test_clear_in_flight_admission_hides_it_and_does_not_route_it(environment, monkeypatch):
    binding, chat, service = environment.binding, environment.panel.chat, environment.service
    entered, proceed = Event(), Event()
    accept = service.accept_input

    def delayed(*args, **kwargs):
        entered.set()
        assert proceed.wait(8)
        return accept(*args, **kwargs)

    monkeypatch.setattr(service, "accept_input", delayed)
    try:
        chat.send_user_message("admission before clear")
        _until(entered.is_set)
        chat._clear_conversation()
        assert not chat.send_user_message("during clear")
    finally:
        proceed.set()
    _until(lambda: not binding.inputs.clearing)
    _idle(binding)
    assert not chat._conversation.get_transcript()
    assert not service.prepare_batch(binding.context)
    assert not environment.client.calls
    assert service.state(binding.context)["ingress"][0]["status"] == "discarded"


def test_real_provider_receives_only_its_request_skill(environment):
    chat, service, binding = environment.panel.chat, environment.service, environment.binding
    skill = {"name": "terms", "prompt": "SKILL_SCOPE_UNIQUE_4321"}
    assert chat.send_user_message("First skilled question", skill_context=skill)
    _until(
        lambda: (
            bool(service.requests(service.state(binding.context)))
            and all(r.terminal for r in service.requests(service.state(binding.context)))
        )
    )
    first = len(environment.client.calls)
    assert "SKILL_SCOPE_UNIQUE_4321" in json.dumps(environment.client.calls[first - 1][0])
    assert chat.send_user_message("Second ordinary question")
    _until(
        lambda: (
            len(service.requests(service.state(binding.context))) == 2
            and all(r.terminal for r in service.requests(service.state(binding.context)))
        )
    )
    assert "SKILL_SCOPE_UNIQUE_4321" not in json.dumps(environment.client.calls[-1][0])


def test_clear_discards_unresolved_receipts_of_partially_applied_batch(environment):
    from transbridge.application.assistant_requests.conversation_visibility import clear_conversation
    from transbridge.application.assistant_requests.models import RequestError
    from transbridge.application.assistant_requests.routing import apply_proposal, parse_proposal
    from transbridge.application.assistant_requests.routing_recovery import resolve_routing_input, unresolved_directives

    service, binding = environment.service, environment.binding
    binding.set_active(False)
    service.accept_input(binding.context, "abc", selection={})
    batch = service.prepare_batch(binding.context)
    source = batch.sources[0]
    proposal = parse_proposal({
        "protocol_version": 1,
        "directives": [
            {
                "local_id": "good",
                "message_id": source.message_id,
                "span": [0, 1],
                "action": "RESPOND",
                "response": "ok",
            },
            {
                "local_id": "bad",
                "message_id": source.message_id,
                "span": [1, 3],
                "action": "CREATE",
                "goal": "bad",
                "items": [],
            },
        ],
    })
    routed = apply_proposal((), batch, proposal)
    service.transact(
        binding.context, lambda state: state.update(batches=[{"batch": routed.batch.to_dict(), "status": "applied"}])
    )
    assert len(list(unresolved_directives(service.state(binding.context)))) == 1
    state = clear_conversation(service, binding.context, [])
    assert not list(unresolved_directives(state))
    assert state["batches"][0]["batch"]["receipts"][0]["status"] == "applied"
    with pytest.raises(RequestError, match="待处理输入已变化"):
        resolve_routing_input(service, binding.context, batch.batch_id, "bad", action="replan")
