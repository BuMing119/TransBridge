from copy import deepcopy
from dataclasses import replace
import json

import pytest

from transbridge.application.assistant_requests.management import (
    clarify_directive,
    reassign_unexecuted_item,
    recover_session_requests,
    successor_request,
)
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    ItemKind,
    ItemStatus,
    RequestError,
    RequestItem,
    RequestStatus,
    UserRequest,
)
from transbridge.application.assistant_requests.routing import (
    RoutingBatch,
    RoutingSource,
    apply_proposal,
    parse_proposal,
)


def _request(request_id="r", **changes):
    return replace(UserRequest(request_id, "s", "goal", (RequestItem("i", "answer"),)), **changes)


def _effect(status=EffectStatus.RUNNING):
    return EffectIntent(
        "e",
        "hash",
        AssistantExecutionRef("r", 1, ("i",), "a", "d", "t", "s"),
        1,
        status=status,
        job_id="job",
        run_id="run",
        approval_id="old-approval",
    )


def _ambiguous_batch():
    proposal = parse_proposal({
        "protocol_version": 1,
        "directives": [
            {"local_id": "cancel", "message_id": "original", "span": [0, 2], "action": "CANCEL"},
            {
                "local_id": "create",
                "message_id": "original",
                "span": [3, 5],
                "action": "CREATE",
                "goal": "new goal",
                "items": [{"item_id": "new-item", "description": "new question", "kind": "answer"}],
            },
        ],
    })
    return apply_proposal(
        (_request(),), RoutingBatch("b", "s", (RoutingSource("original", "取消那个，解释术语"),)), proposal
    )


@pytest.mark.parametrize(
    "status", [RequestStatus.COMPLETED, RequestStatus.CANCELLED, RequestStatus.FAILED, RequestStatus.SUPERSEDED]
)
def test_terminal_continue_creates_new_authority_and_retains_only_retry_provenance(status):
    original = _request(
        status=status,
        effects=(_effect(EffectStatus.SUCCEEDED),),
        pause_reasons=("user_paused",),
        items=(RequestItem("i", "answer", status=ItemStatus.SATISFIED, evidence_ids=("old-answer",)),),
    )
    successor = successor_request(original, "new-user-input", "new-request")
    assert successor.request_id == "new-request" and successor.related_to == "r"
    assert successor.revision == 1 and successor.status == RequestStatus.OPEN
    assert successor.effects == successor.dispatches == successor.pause_reasons == ()
    assert successor.items[0].status == ItemStatus.PENDING and successor.items[0].evidence_ids == ()
    assert successor.source_message_ids == ("new-user-input",)
    assert successor.evidence[0].kind == "retry_of" and successor.evidence[0].reference == "r"
    assert "old-approval" not in json.dumps(successor.to_dict())
    assert original.status == status and original.effects[0].approval_id == "old-approval"


@pytest.mark.parametrize(
    "original",
    [
        _request(pause_reasons=("user_paused",)),
        _request(status=RequestStatus.STOPPING),
        _request(status=RequestStatus.FAILED, effects=(_effect(),)),
    ],
)
def test_open_or_unsettled_requests_cannot_be_replayed_as_successors(original):
    with pytest.raises(RequestError, match="REQUEST_NOT_SETTLED"):
        successor_request(original, "user", "new")


def test_clarification_updates_only_unresolved_receipt_and_keeps_original_proposal_audit():
    original = _ambiguous_batch()
    applied_before = next(r for r in original.batch.receipts if r.local_id == "create")
    clarification = RoutingSource("clarification", "取消的是第一个请求")
    result = clarify_directive(original.requests, original.batch, "cancel", "r", clarification)
    assert len(result.requests) == len(original.requests) == 2
    assert next(r for r in result.requests if r.request_id == "r").status == RequestStatus.CANCELLED
    assert next(r for r in result.batch.receipts if r.local_id == "create") == applied_before
    assert result.batch.proposal == original.batch.proposal
    assert result.batch.proposal_digest == original.batch.proposal_digest
    old_receipt = next(r for r in original.batch.receipts if r.local_id == "cancel")
    new_receipt = next(r for r in result.batch.receipts if r.local_id == "cancel")
    assert new_receipt.status == "applied" and new_receipt.directive_id == old_receipt.directive_id
    audit = json.loads(new_receipt.diagnostic)
    assert audit["clarification_message_id"] == "clarification"
    assert audit["previous_diagnostic"] == old_receipt.diagnostic
    restored = RoutingBatch.from_dict(json.loads(json.dumps(result.batch.to_dict())))
    assert apply_proposal(result.requests, restored, restored.proposal).requests == result.requests
    with pytest.raises(RequestError, match="REQUEST_CLARIFICATION_INVALID"):
        clarify_directive(result.requests, restored, "cancel", "r", RoutingSource("again", "again"))


def test_clarification_targets_current_revision_without_replaying_applied_create():
    original = _ambiguous_batch()
    current = tuple(replace(r, revision=3) if r.request_id == "r" else r for r in original.requests)
    result = clarify_directive(current, original.batch, "cancel", "r", RoutingSource("clarify", "this one"))
    receipt = next(r for r in result.batch.receipts if r.local_id == "cancel")
    assert receipt.status == "applied" and json.loads(receipt.diagnostic)["expected_revision"] == 3


@pytest.mark.parametrize(
    "source",
    [
        RoutingSource("new", "text", kind="tool"),
        RoutingSource("new", "text", (("project", "other"),)),
        RoutingSource("original", "changed text"),
    ],
)
def test_clarification_rejects_material_wrong_scope_and_reused_input(source):
    original = _ambiguous_batch()
    with pytest.raises(RequestError):
        clarify_directive(original.requests, original.batch, "cancel", "r", source)


def test_clarification_cannot_mutate_a_request_in_another_session():
    original = _ambiguous_batch()
    cross_session = tuple(replace(r, session_id="other") if r.request_id == "r" else r for r in original.requests)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        clarify_directive(cross_session, original.batch, "cancel", "r", RoutingSource("new", "text"))


def test_unexecuted_independent_item_can_move_atomically_with_revision_audit():
    source = _request(items=(RequestItem("move", "move me"), RequestItem("stay", "stay here")))
    target = _request("target", items=(RequestItem("target-item", "existing"),))
    new_source, new_target = reassign_unexecuted_item(
        source, target, "move", source_message_id="correction", expected_source_revision=1, expected_target_revision=1
    )
    assert [i.item_id for i in new_source.items] == ["stay"]
    assert [i.item_id for i in new_target.items] == ["target-item", "move"]
    assert new_source.revision == new_target.revision == 2
    assert new_source.revisions[0].items == source.items and new_target.revisions[0].items == target.items
    assert new_source.source_message_ids[-1] == new_target.source_message_ids[-1] == "correction"


@pytest.mark.parametrize(
    "changes",
    [
        {"effects": (_effect(),)},
        {"items": (RequestItem("move", "move", waiting_reasons=("confirmation",)), RequestItem("stay", "stay"))},
        {"items": (RequestItem("move", "move"), RequestItem("stay", "stay", dependencies=("move",)))},
    ],
)
def test_executed_approval_or_dependent_items_cannot_be_reassigned(changes):
    source = _request(items=(RequestItem("move", "move"), RequestItem("stay", "stay")))
    source = replace(source, **changes)
    target = _request("target", items=(RequestItem("target-item", "existing"),))
    with pytest.raises(RequestError, match="REQUEST_REASSIGN_UNSAFE"):
        reassign_unexecuted_item(
            source,
            target,
            "move",
            source_message_id="correction",
            expected_source_revision=1,
            expected_target_revision=1,
        )


def test_recovery_preserves_unknown_manifest_fields_and_waits_for_approval_and_unknown_outcome():
    request = _request(
        items=(RequestItem("i", "execute", kind=ItemKind.EXECUTION, waiting_reasons=("confirmation",)),),
        effects=(_effect(),),
    )
    record = {**request.to_dict(), "migration_note": {"legacy_missing_history": True}}
    state = {"requests": [record], "schema_version": 4, "ingress": [{"status": "pending"}], "other": {"x": [1]}}
    before = deepcopy(state)
    recovered = recover_session_requests(state, live_job_ids=())
    result = recovered["requests"][0]
    assert result["effects"][0]["status"] == EffectStatus.OUTCOME_UNKNOWN
    assert "approval_revalidation" in result["items"][0]["waiting_reasons"]
    assert result["migration_note"] == record["migration_note"]
    assert recovered["ingress"] == state["ingress"] and recovered["other"] == state["other"]
    assert state == before


def test_recovery_uses_proven_receipt_but_does_not_infer_success_from_missing_job():
    request = _request(items=(RequestItem("i", "execute", kind=ItemKind.EXECUTION),), effects=(_effect(),))
    state = {"requests": [request.to_dict()]}
    recovered = recover_session_requests(state, proven_outcomes={"e": ("succeeded", "commit-receipt")})
    assert recovered["requests"][0]["status"] == RequestStatus.COMPLETED
    assert recovered["requests"][0]["effects"][0]["receipt"] == "commit-receipt"
    live = recover_session_requests(state, live_job_ids=("job",))
    assert live["requests"][0]["effects"][0]["status"] == EffectStatus.RUNNING
    assert live["requests"][0]["status"] == RequestStatus.OPEN
