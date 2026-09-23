"""Routing changes, direct answers and their tool receipt commit together."""

from threading import Event

import pytest

from tests.application.assistant_requests.test_request_repository import _proposal, _snapshot
from transbridge.application.assistant_requests.control_operations import recover_control_calls
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.routing_results import run_routing_call

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _setup(composed):
    _, service, context = composed
    service.accept_input(context, "question", selection={}, command_id="input")
    batch = service.prepare_batch(context)
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.acquire_routing(context.session_id, "view")
    proposal = _proposal(batch)
    parsed = {"tool_call_id": "routing", "control": "submit_request_routing", "arguments": proposal}
    parent = {
        "role": "assistant",
        "content": "",
        "message_id": "parent",
        "control_turn_id": admission.turn_id,
        "control_batch_id": batch.batch_id,
        "tool_calls": [{"id": "routing", "name": "submit_request_routing", "arguments": proposal}],
    }
    return service, context, admission, batch, parsed, parent


def test_routing_and_receipt_commit_once_and_replay_after_release(composed):
    service, context, admission, batch, parsed, parent = _setup(composed)
    first = run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=lambda: False)
    service.scheduler.release(admission)
    assert (
        run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=lambda: False) == first
    )
    assert len(service.requests(service.state(context))) == 1
    tools = [m for m in _snapshot(composed[0], context).backend_messages() if m["role"] == "tool"]
    assert tools == [first]


def test_cancelled_routing_saves_error_without_consuming_sources(composed):
    service, context, admission, batch, parsed, parent = _setup(composed)
    cancelled = Event()
    cancelled.set()
    receipt = run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=cancelled.is_set)
    assert receipt["is_error"]
    assert not service.requests(service.state(context))
    assert service.state(context)["ingress"][0]["status"] == "routing"


def test_direct_answer_and_receipt_survive_replay_without_duplicates(composed):
    service, context, admission, batch, parsed, parent = _setup(composed)
    parsed["arguments"]["directives"] = [
        {"local_id": "reply", "message_id": "input", "span": [0, 8], "action": "RESPOND", "response": "answer"}
    ]
    first = run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=lambda: False)
    service.scheduler.release(admission)
    assert (
        run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=lambda: False) == first
    )
    messages = _snapshot(composed[0], context).backend_messages()
    assert [message for message in messages if message["role"] == "tool"] == [first]
    assert [message["content"] for message in messages if message.get("message_id", "").startswith("reply-")] == [
        "answer"
    ]
    assert not service.requests(service.state(context))
    assert service.state(context)["ingress"][0]["status"] == "applied"


def test_invalid_direct_answer_records_failure_without_consuming_input(composed):
    service, context, admission, batch, parsed, parent = _setup(composed)
    parsed["arguments"]["directives"] = [
        {"local_id": "reply", "message_id": "input", "span": [0, 8], "action": "RESPOND", "response": ""}
    ]
    receipt = run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=lambda: False)
    assert receipt["is_error"]
    assert service.state(context)["ingress"][0]["status"] == "routing"
    assert not any(
        message.get("message_id", "").startswith("reply-")
        for message in _snapshot(composed[0], context).backend_messages()
    )


def test_receipt_staging_failure_keeps_routing_unapplied(composed, monkeypatch):
    service, context, admission, batch, parsed, parent = _setup(composed)
    original = service.with_transcript

    def fail(snapshot, records, **kwargs):
        if any(m.get("tool_call_id") == "routing" for m in records):
            raise RequestError("STORAGE_FAILED", "routing receipt disk failure")
        return original(snapshot, records, **kwargs)

    monkeypatch.setattr(service, "with_transcript", fail)
    with pytest.raises(RequestError, match="STORAGE_FAILED"):
        run_routing_call(service, context, admission, batch.batch_id, parsed, parent, cancelled=lambda: False)
    assert not service.requests(service.state(context))
    assert not any(m["role"] == "tool" for m in _snapshot(composed[0], context).backend_messages())


def test_recovery_closes_pending_routing_parent_without_replaying_proposal(composed):
    service, context, admission, _, _, parent = _setup(composed)
    service.save_history(context, [parent])
    service.scheduler.release(admission)
    result = recover_control_calls(service, context)
    assert len(result) == 1 and result[0]["is_error"]
    assert not service.requests(service.state(context))
    assert recover_control_calls(service, context) == []
