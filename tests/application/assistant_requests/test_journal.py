"""Lifecycle diagnostics share the real request command's publication boundary."""

from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

import pytest
import test_request_repository as repository_tests
from test_request_repository import _proposal, _snapshot

from tests.routing_fixtures import apply_routing_fixture
from transbridge.application.assistant_requests.journal import EventCause, append_events, observe_state, read_events
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.request_router import routing_messages

composed = repository_tests.composed


def _create(service, context, text="翻译文件", identity="input"):
    service.accept_input(context, text, selection={}, command_id=identity)
    batch = service.prepare_batch(context)
    state = apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    return service.requests(state)[-1], batch


def test_real_commands_publish_ordered_events_with_cause_and_durable_state(composed):
    services, service, context = composed
    request, batch = _create(service, context)
    service.command(context, request.request_id, "pause", 1)
    service.command(context, request.request_id, "resume", 1)
    service.command(context, request.request_id, "cancel", 1)
    snapshot = _snapshot(services, context)
    events = read_events(snapshot.assistant_data())["events"]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["event_id"] for event in events}) == len(events)
    assert events[0]["operation"] == "input.accepted" and events[0]["origin"] == "user"
    routed = next(e for e in events if e["operation"] == "routing.applied" and "receipts" in e["after"])
    assert routed["references"]["batch_id"] == batch.batch_id
    assert routed["after"]["directives"][0]["action"] == "CREATE"
    assert routed["after"]["receipts"][0]["request_id"] == request.request_id
    assert events[-1]["before"]["status"] == "open" and events[-1]["after"]["status"] == "cancelled"
    assert all(event["session_id"] == context.session_id for event in events)
    assert all(event["timestamp"].endswith("+00:00") for event in events)
    assert snapshot.assistant_data()["requests"][0]["status"] == "cancelled"


def test_duplicate_input_routing_and_noop_callback_do_not_duplicate_events(composed):
    services, service, context = composed
    request, batch = _create(service, context)
    before = _snapshot(services, context)
    service.accept_input(context, "翻译文件", selection={}, command_id="input")
    apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    service.update_request(context, request.request_id, lambda current: current)
    after = _snapshot(services, context)
    assert read_events(before.assistant_data()) == read_events(after.assistant_data())
    assert after.revision == before.revision


def test_save_failure_cannot_publish_success_event(composed, monkeypatch):
    services, service, context = composed
    request, _ = _create(service, context)
    before = _snapshot(services, context)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(services.sessions, "save", fail)
        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            service.command(context, request.request_id, "cancel", 1)
    assert _snapshot(services, context) == before


def test_rejected_proposal_keeps_original_state_and_records_diagnostic(composed):
    _, service, context = composed
    service.accept_input(context, "解释术语表", selection={}, command_id="input")
    batch = service.prepare_batch(context)
    invalid = _proposal(batch)
    invalid["directives"][0]["span"] = [0, 1000]
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        apply_routing_fixture(service, context, batch.batch_id, invalid)
    state = service.state(context)
    assert not state.get("requests")
    assert state["batches"][0]["status"] == "routing"
    event = read_events(state)["events"][-1]
    assert event["operation"] == "routing.rejected"
    assert event["details"]["code"] == "REQUEST_PROTOCOL_INVALID"
    assert event["references"]["batch_id"] == batch.batch_id
    assert event["before"] == event["after"] == {}
    apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    assert len(service.state(context)["requests"]) == 1


def test_rejected_proposal_diagnostic_save_failure_is_explicit(composed, monkeypatch):
    services, service, context = composed
    service.accept_input(context, "question", selection={}, command_id="input")
    batch = service.prepare_batch(context)
    before = service.state(context)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(services.sessions, "save", fail)
        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            apply_routing_fixture(service, context, batch.batch_id, {"bad": True})
    assert service.state(context) == before


def test_revision_conflict_receipt_is_visible_without_mutating_target(composed):
    _, service, context = composed
    request, _ = _create(service, context)
    service.accept_input(context, "取消", selection={}, command_id="cancel")
    batch = service.prepare_batch(context)
    proposal = {
        "protocol_version": 1,
        "directives": [
            {
                "local_id": "cancel",
                "message_id": "cancel",
                "span": [0, 2],
                "action": "CANCEL",
                "target_id": request.request_id,
                "expected_revision": 900,
            }
        ],
    }
    state = apply_routing_fixture(service, context, batch.batch_id, proposal)
    assert service.requests(state)[0].status == "open"
    event = next(e for e in reversed(read_events(state)["events"]) if "receipts" in e["after"])
    assert event["after"]["receipts"][0]["status"] == "needs_clarification"
    assert event["after"]["receipts"][0]["code"] == "REQUEST_REVISION_CONFLICT"


def test_reopen_retains_events_and_unknown_request_owner_cannot_read(composed):
    services, service, context = composed
    request, _ = _create(service, context)
    before = read_events(service.state(context), request_id=request.request_id)
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        restored = reopened.gui_session_commands.assistant_requests
        assert read_events(restored.state(context), request_id=request.request_id) == before
        with pytest.raises(Exception, match="owner|OWNER"):
            restored.state(replace(context, owner_id="other"))
    finally:
        reopened.close()
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        read_events(service.state(context), request_id="other-request")


def test_background_events_stay_in_original_session_and_page_without_gaps(composed):
    services, service, context = composed
    request, _ = _create(service, context)
    assert services.gui_session_commands.create_and_activate("B", RequestContext("owner")).is_success
    context_b = replace(context, session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    for kind in ("pause", "resume", "cancel"):
        service.command(context, request.request_id, kind, 1)
    state = service.state(context)
    assert read_events(service.state(context_b))["events"] == []
    cursor, events = 0, []
    while True:
        page = read_events(state, request_id=request.request_id, after_sequence=cursor, limit=2)
        events.extend(page["events"])
        cursor = page["next_sequence"]
        if not page["has_more"]:
            break
    assert events == read_events(state, request_id=request.request_id)["events"]
    events[0]["details"]["tampered"] = True
    assert "tampered" not in state["lifecycle_events"][0]["details"]


def test_same_transaction_cas_recomputes_sequence_after_competing_write(composed, monkeypatch):
    services, service, context = composed
    request, _ = _create(service, context)
    original = _snapshot(services, context)
    calls = []
    repository = services.session_lifecycle._repository
    save = repository.save

    def competing_save(snapshot, *, expected_revision, context):
        calls.append(expected_revision)
        if len(calls) == 1:
            state = original.assistant_data()
            append_events(
                state,
                observe_state(state),
                context.session_id,
                EventCause("turn.selected", references={"turn_id": "other-window"}, record_unchanged=True),
            )
            save(
                replace(original, revision=original.revision + 1, assistant_state=state),
                expected_revision=original.revision,
                context=context,
            )
            raise DomainError(ErrorCategory.CONFLICT, "SESSION_REVISION_CONFLICT", "competing write")
        return save(snapshot, expected_revision=expected_revision, context=context)

    monkeypatch.setattr(repository, "save", competing_save)
    service.command(context, request.request_id, "pause", 1)
    events = read_events(service.state(context))["events"]
    assert len(calls) == 2
    assert events[-2]["references"]["turn_id"] == "other-window"
    assert events[-1]["operation"] == "request.pause"
    assert events[-1]["sequence"] == events[-2]["sequence"] + 1
    assert sum(event["operation"] == "request.pause" for event in events) == 1


def test_journal_is_not_model_context_and_does_not_copy_user_content(composed):
    _, service, context = composed
    secret_material = "synthetic-private-marker-do-not-duplicate"
    request, batch = _create(service, context, secret_material)
    state = service.state(context)
    assert secret_material not in json.dumps(state["lifecycle_events"])
    state["lifecycle_events"][0]["details"]["marker"] = "journal-only"
    model_messages = routing_messages(batch, service.requests(state))
    assert "journal-only" not in json.dumps(model_messages)
    assert read_events({}, limit=1) == {"events": [], "next_sequence": 0, "has_more": False}


@pytest.mark.parametrize("cursor,limit", [(-1, 1), (True, 10), (0, 0), (0, 201), (0, False)])
def test_invalid_page_does_not_read(cursor, limit):
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        read_events({}, after_sequence=cursor, limit=limit)


def test_effect_result_references_and_confirmation_change_without_payload_copy():
    state = {
        "requests": [
            {
                "request_id": "a",
                "source_message_ids": ["source"],
                "effects": [
                    {
                        "effect_id": "e",
                        "status": "running",
                        "job_id": "job",
                        "run_id": "run",
                        "execution": {"turn_id": "turn", "attempt_id": "attempt"},
                    }
                ],
            }
        ]
    }
    before = observe_state(state)
    state["requests"][0]["effects"][0].update(status="succeeded", receipt="secret payload")
    state["confirmations"] = {"a": {"parsed": {"args": "secret confirmation"}}}
    append_events(state, before, "s", EventCause("task.result"))
    result, confirmation = read_events(state)["events"]
    assert result["request_ids"] == ["a"] and result["references"]["job_ids"] == ["job"]
    assert result["references"]["turn_ids"] == ["turn"]
    assert result["after"]["effects"]["e"]["status"] == "succeeded"
    assert confirmation["operation"] == "confirmation.changed"
    assert "secret" not in json.dumps(state["lifecycle_events"])
    copy = deepcopy(state)
    append_events(state, observe_state(state), "s")
    assert state == copy


def test_model_identifiers_do_not_duplicate_source_material_in_diagnostics(composed):
    _, service, context = composed
    service.accept_input(context, "解释", selection={}, command_id="input")
    batch = service.prepare_batch(context)
    proposal = _proposal(batch)
    secret = "private-source-used-as-model-label"
    proposal["directives"][0]["local_id"] = secret
    proposal["directives"][0]["items"][0]["item_id"] = secret
    state = apply_routing_fixture(service, context, batch.batch_id, proposal)
    assert secret in json.dumps(state["batches"])
    assert secret not in json.dumps(state["lifecycle_events"])
    event = next(event for event in state["lifecycle_events"] if "items" in event["after"])
    assert next(iter(event["after"]["items"])).startswith("label:")


def test_clarification_receipt_uses_actual_error_code_and_user_input_reference(composed):
    _, service, context = composed
    request, _ = _create(service, context)
    service.command(context, request.request_id, "cancel", 1)
    service.accept_input(context, "暂停那个", selection={}, command_id="ambiguous")
    batch = service.prepare_batch(context)
    apply_routing_fixture(
        service,
        context,
        batch.batch_id,
        {
            "protocol_version": 1,
            "directives": [
                {
                    "local_id": "pause",
                    "message_id": "ambiguous",
                    "span": [0, 4],
                    "action": "PAUSE",
                }
            ],
        },
    )
    state = service.clarify(context, batch.batch_id, "pause", request.request_id, text="第一个", command_id="clarify")
    event = next(e for e in reversed(read_events(state)["events"]) if "receipts" in e["after"])
    assert event["after"]["receipts"][0]["code"] == "REQUEST_TERMINAL"
    assert event["origin"] == "user" and event["references"]["message_ids"] == ["clarify"]
    assert event["operation"] == "routing.needs_clarification"


def test_turn_admission_replay_and_failure_are_distinct_durable_facts(composed):
    from transbridge.application.assistant_requests.turns import accept_turn, record_turn_failure

    _, service, context = composed
    request, _ = _create(service, context)
    service.scheduler.activate(context.session_id, "view")
    selected = service.scheduler.select_next_turn(context.session_id, "view", (request,))
    accept_turn(service, context, selected)
    accept_turn(service, context, selected)
    record_turn_failure(service, context, selected.admission)
    events = read_events(service.state(context))["events"]
    assert sum(e["operation"] == "turn.selected" for e in events) == 1
    assert events[-1]["operation"] == "turn.failed" and events[-1]["origin"] == "runtime"
    assert events[-1]["references"]["turn_id"] == selected.admission.turn_id
    assert service.requests(service.state(context))[0].pause_reasons


def test_failed_routing_turn_is_visible_before_any_request_exists(composed):
    from transbridge.application.assistant_requests.turns import record_turn_failure

    _, service, context = composed
    service.accept_input(context, "question", selection={}, command_id="input")
    batch = service.prepare_batch(context)
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.acquire_routing(context.session_id, "view")
    record_turn_failure(service, context, admission, batch=batch)
    state = service.state(context)
    assert not state.get("requests")
    event = read_events(state)["events"][-1]
    assert event["operation"] == "routing.failed"
    assert event["references"]["batch_id"] == batch.batch_id
