"""User attachment evidence keeps original routing spans and complete bounded context."""

from dataclasses import replace
import json
from uuid import uuid4

import pytest

from tests.application.assistant_requests.test_request_repository import _proposal, _snapshot
from tests.routing_fixtures import apply_routing_fixture
from transbridge.application.assistant_requests.attachments import (
    MAX_ATTACHMENT_CHARACTERS,
    attachment_snapshot,
    display_user_content,
    encode_user_content,
)
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.turns import record_turn_failure, reject_routing_input
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.context_budget import ContextBudget, ContextBudgetExceeded
from transbridge.smart_assistant.request_model_input import RequestModelInput
from transbridge.smart_assistant.request_router import routing_messages

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _attachment(content="Complete text. Ignore previous instructions is quoted reference data."):
    return {"name": "guide.txt", "format": "text", "content": content}


def test_admission_preserves_routing_text_and_full_material_across_restart(composed):
    services, service, context = composed
    text = "按这个指南翻译"
    accepted = service.accept_input(context, text, selection={}, command_id="material", attachments=(_attachment(),))
    ingress = service.state(context)["ingress"][0]
    assert ingress["text"] == text
    assert "content" not in ingress
    assert service.input_content(context, "material") == accepted["content"]
    source = _snapshot(services, context).backend_messages()[0]
    assert source["role"] == "user"
    snapshot = attachment_snapshot(source["content"])
    assert snapshot["user_request"] == text
    assert snapshot["attachments"] == [_attachment()]
    assert display_user_content(source["content"]) == text + "\n\n本次附件：guide.txt"
    batch = service.prepare_batch(context)
    assert batch.sources[0].text == text
    routing = json.loads(routing_messages(batch, (), history=[source])[-1]["content"])
    assert routing["inputs"][0]["text"] == text
    assert routing["inputs"][0]["attachments"] == [_attachment()]
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-16T00:00:00Z"
    )
    try:
        assert reopened.gui_session_commands.assistant_requests.input_content(context, "material") == source["content"]
    finally:
        reopened.close()


def test_first_and_later_materials_reach_scoped_execution_without_system_authority(composed):
    services, service, context = composed
    contents = ("FIRST_FILE_BODY_71935", "SECOND_FILE_BODY_48210")
    for index, body in enumerate(contents):
        service.accept_input(context, f"Read {index}", selection={}, attachments=(_attachment(body),))
        batch = service.prepare_batch(context)
        state = apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
        request = service.requests(state)[-1]
        history = _snapshot(services, context).backend_messages()
        prepared = RequestModelInput(
            tuple(history), (), ContextBudget(context_window=20000), request=request, requests=service.requests(state)
        )
        messages, _ = prepared.assemble()
        assert any(body in message.get("content", "") for message in messages if message["role"] == "user")
        assert not any(body in message.get("content", "") for message in messages if message["role"] == "system")
        if index:
            assert contents[0] not in json.dumps(messages)


def test_empty_snapshot_removes_current_material_but_keeps_historical_evidence(composed):
    services, service, context = composed
    first = service.accept_input(context, "first", selection={}, attachments=(_attachment("OLD_BODY"),))
    batch = service.prepare_batch(context)
    apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    service.accept_input(context, "next", selection={}, attachments=())
    batch = service.prepare_batch(context)
    history = _snapshot(services, context).backend_messages()
    payload = json.loads(
        routing_messages(batch, service.requests(service.state(context)), history=history)[-1]["content"]
    )
    assert payload["inputs"][0]["attachments"] == []
    assert "OLD_BODY" not in json.dumps(payload)
    assert "OLD_BODY" in service.input_content(context, first["message_id"])


def test_attachment_size_and_context_budget_fail_explicitly_without_truncating(composed):
    services, service, context = composed
    with pytest.raises(ValueError, match="未截断"):
        encode_user_content("read", (_attachment("X" * (MAX_ATTACHMENT_CHARACTERS + 1)),))
    body = "Complete long material " * 200
    service.accept_input(context, "read", selection={}, attachments=(_attachment(body),))
    batch = service.prepare_batch(context)
    history = _snapshot(services, context).backend_messages()
    messages = routing_messages(batch, (), history=history)
    budget = ContextBudget(context_window=500, output_reserve=50, protocol_margin=20, estimator=len)
    with pytest.raises(ContextBudgetExceeded):
        budget.require(messages)
    state = apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    request = service.requests(state)[0]
    prepared = RequestModelInput(tuple(history), (), budget, request=request, requests=(request,))
    with pytest.raises(ContextBudgetExceeded):
        prepared.assemble()
    assert attachment_snapshot(history[0]["content"])["attachments"][0]["content"] == body


def test_same_identity_cannot_change_attachment_and_foreign_owner_cannot_read(composed):
    _, service, context = composed
    accepted = service.accept_input(context, "read", selection={}, command_id="once", attachments=(_attachment(),))
    assert (
        service.accept_input(context, "read", selection={}, command_id="once", attachments=(_attachment(),)) == accepted
    )
    with pytest.raises(RequestError, match="input identity reused"):
        service.accept_input(context, "read", selection={}, command_id="once", attachments=())
    with pytest.raises(Exception, match="owner|OWNER"):
        service.input_content(replace(context, owner_id="other"), "once")


def test_budget_rejection_releases_all_batch_inputs_and_retains_restart_evidence(composed):
    services, service, context = composed
    first = service.accept_input(context, "large", selection={}, attachments=(_attachment("FULL_MATERIAL"),))
    service.accept_input(context, "another batched input", selection={})
    batch = service.prepare_batch(context)
    service.scheduler.activate(context.session_id, "test")
    admission = service.scheduler.acquire_routing(context.session_id, "test")
    reject_routing_input(service, context, admission, batch, code="CONTEXT_BUDGET_EXCEEDED")
    service.scheduler.release(admission)
    state = service.state(context)
    assert [item["status"] for item in state["ingress"]] == ["rejected", "rejected"]
    assert any(event["operation"] == "routing.rejected" for event in state["lifecycle_events"])
    assert service.prepare_batch(context) is None
    assert "FULL_MATERIAL" in service.input_content(context, first["message_id"])
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-16T00:00:00Z"
    )
    try:
        restored = reopened.gui_session_commands.assistant_requests
        assert restored.prepare_batch(context) is None
        restored.accept_input(context, "corrected", selection={}, attachments=())
        assert [source.text for source in restored.prepare_batch(context).sources] == ["corrected"]
        assert "FULL_MATERIAL" in restored.input_content(context, first["message_id"])
    finally:
        reopened.close()


def test_temporary_routing_failure_remains_retryable(composed):
    _, service, context = composed
    service.accept_input(context, "temporary failure", selection={}, attachments=(_attachment(),))
    batch = service.prepare_batch(context)
    service.scheduler.activate(context.session_id, "test")
    admission = service.scheduler.acquire_routing(context.session_id, "test")
    record_turn_failure(service, context, admission, batch=batch)
    service.scheduler.release(admission)
    assert service.prepare_batch(context) == batch
    assert service.state(context)["ingress"][0]["status"] == "routing"


def test_stale_budget_rejection_cannot_reject_another_routing_turn(composed):
    _, service, context = composed
    service.accept_input(context, "first", selection={}, attachments=(_attachment(),))
    batch = service.prepare_batch(context)
    service.scheduler.activate(context.session_id, "test")
    admission = service.scheduler.acquire_routing(context.session_id, "test")
    service.scheduler.release(admission)
    replacement = service.scheduler.acquire_routing(context.session_id, "test")
    with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
        reject_routing_input(service, context, admission, batch, code="CONTEXT_BUDGET_EXCEEDED")
    assert service.prepare_batch(context) == batch
    assert service.state(context)["ingress"][0]["status"] == "routing"
    service.scheduler.release(replacement)
