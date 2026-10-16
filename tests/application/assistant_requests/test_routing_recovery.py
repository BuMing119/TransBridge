"""A rejected directive has a bounded explicit exit without replaying accepted work."""

from copy import deepcopy
from dataclasses import replace

import pytest
import test_request_repository as repository_tests
from test_request_repository import _snapshot

from tests.routing_fixtures import apply_routing_fixture
from transbridge.application.assistant_requests.attachments import attachment_snapshot
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.routing import RoutingBatch, apply_proposal
from transbridge.application.assistant_requests.routing_recovery import (
    resolution_kind,
    resolve_routing_input,
    unresolved_directives,
)
from transbridge.application.assistant_requests.skill_context import request_skill_contexts

composed = repository_tests.composed


def _partial(service, context):
    service.accept_input(
        context,
        "解释术语；翻译所选内容",
        command_id="original",
        selection={"selected_entry_keys": ["original-key"]},
        attachments=[{"name": "guide.txt", "format": "txt", "content": "Use plain language."}],
        skill_context={"name": "plain", "prompt": "Keep the requested terminology."},
    )
    batch = service.prepare_batch(context)
    state = apply_routing_fixture(
        service,
        context,
        batch.batch_id,
        {
            "protocol_version": 1,
            "directives": [
                {
                    "local_id": "done",
                    "message_id": "original",
                    "span": [0, 4],
                    "action": "CREATE",
                    "goal": "explain",
                    "items": [{"item_id": "answer", "description": "explain", "kind": "answer"}],
                },
                {
                    "local_id": "broken",
                    "message_id": "original",
                    "span": [5, 11],
                    "action": "CREATE",
                    "goal": "translate",
                    "items": [],
                },
                {"local_id": "ambiguous", "message_id": "original", "span": [5, 11], "action": "CANCEL"},
            ],
        },
    )
    return batch.batch_id, state


def test_structure_errors_and_legacy_receipts_never_offer_target_selection(composed):
    _, service, context = composed
    _, state = _partial(service, context)
    pending = list(unresolved_directives(state))
    assert [(receipt.local_id, kind) for _, receipt, kind in pending] == [("broken", "replan"), ("ambiguous", "target")]
    batch, receipt, _ = pending[0]
    assert receipt.status == "needs_replan"
    assert resolution_kind(batch, replace(receipt, status="needs_clarification")) == "replan"
    assert (
        resolution_kind(batch, replace(pending[1][1], diagnostic="REQUEST_REVISION_CONFLICT: old target")) == "replan"
    )
    with pytest.raises(RequestError, match="REQUEST_CLARIFICATION_INVALID"):
        service.clarify(context, batch.batch_id, "broken", service.requests(state)[0].request_id, text="这个任务")


def test_replan_preserves_applied_directive_and_references_only_unaccepted_span(composed):
    services, service, context = composed
    identity, before = _partial(service, context)
    old_batch = RoutingBatch.from_dict(before["batches"][0]["batch"])
    after = resolve_routing_input(service, context, identity, "broken", action="replan", command_id="retry")
    assert after["requests"] == before["requests"]
    new_batch = RoutingBatch.from_dict(after["batches"][0]["batch"])
    assert new_batch.proposal == old_batch.proposal and new_batch.proposal_digest == old_batch.proposal_digest
    assert new_batch.receipts[0] == old_batch.receipts[0]
    assert new_batch.receipts[1].status == "replanned"
    assert apply_proposal(service.requests(after), new_batch, new_batch.proposal).batch == new_batch
    retry = after["ingress"][-1]
    assert retry["status"] == "pending" and "解释术语" not in retry["text"] and "翻译所选内容" in retry["text"]
    assert retry["selection"] == before["ingress"][0]["selection"]
    assert retry["context"] == before["ingress"][0]["context"]
    material = attachment_snapshot(service.input_content(context, "retry"))
    assert material["attachments"][0]["content"] == "Use plain language."
    saved = next(m for m in _snapshot(services, context).backend_messages() if m["message_id"] == "retry")
    assert saved["skill_context"] == retry["skill_context"] == before["ingress"][0]["skill_context"]
    assert service.prepare_batch(context).sources[0].message_id == "retry"


def test_repeated_resolution_never_replays_or_creates_duplicate_ingress(composed):
    _, service, context = composed
    identity, _ = _partial(service, context)
    first = resolve_routing_input(service, context, identity, "broken", action="replan", command_id="retry")
    assert resolve_routing_input(service, context, identity, "broken", action="replan", command_id="retry") == first
    with pytest.raises(RequestError, match="REQUEST_CLARIFICATION_INVALID"):
        resolve_routing_input(service, context, identity, "broken", action="replan", command_id="double-click")
    assert service.state(context) == first


def test_discard_is_durable_and_clear_boundary_cannot_be_resurrected(composed):
    _, service, context = composed
    identity, _ = _partial(service, context)
    state = resolve_routing_input(service, context, identity, "broken", action="discard")
    assert [receipt.local_id for _, receipt, _ in unresolved_directives(state)] == ["ambiguous"]
    assert service.prepare_batch(context) is None
    service.transact(context, lambda value: value["batches"][0].update(status="discarded"))
    assert not list(unresolved_directives(service.state(context)))
    with pytest.raises(RequestError, match="REQUEST_CLARIFICATION_INVALID"):
        resolve_routing_input(service, context, identity, "ambiguous", action="replan")


def test_changed_scope_and_write_failure_leave_original_input_recoverable(composed, monkeypatch):
    services, service, context = composed
    identity, before = _partial(service, context)
    with pytest.raises(RequestError):
        resolve_routing_input(service, replace(context, project_id="other"), identity, "broken", action="replan")
    assert service.state(context) == before
    snapshot = deepcopy(_snapshot(services, context).backend_messages())
    monkeypatch.setattr(
        service, "with_transcript", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(RequestError):
        resolve_routing_input(service, context, identity, "broken", action="replan")
    assert service.state(context) == before
    assert _snapshot(services, context).backend_messages() == snapshot


def test_restart_retains_selected_skills_in_authorized_new_source(composed):
    services, service, context = composed
    _, state = _partial(service, context)
    old = service.requests(state)[0]
    service.command(context, old.request_id, "cancel", old.revision)
    successor = service.restart(context, old.request_id, text="重新处理全部要求", command_id="restart")
    records = _snapshot(services, context).backend_messages()
    assert request_skill_contexts(records, successor) == request_skill_contexts(records, old)
    saved = next(m for m in records if m["message_id"] == "restart")
    assert len(saved["skill_contexts"]) == 1
