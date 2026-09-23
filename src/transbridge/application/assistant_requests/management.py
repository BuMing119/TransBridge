"""Explicit request correction and restart operations, independent of UI/storage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import fields, replace
import json
from typing import Any

from .models import Evidence, ItemStatus, RequestError, UserRequest, digest
from .recovery import recover_request
from .reducer import RequestEvent, reduce_request, require_open
from .routing import RoutingBatch, RoutingResult, RoutingSource, apply_proposal, parse_proposal


def successor_request(request: UserRequest, source_message_id: str, new_id: str) -> UserRequest:
    """Explicit continuation of a settled terminal request creates fresh authority.

    The caller must atomically persist the new ID and accepted user source. A
    paused OPEN request uses resume instead; STOPPING/unknown outcomes must first
    settle. Prior effects, tokens and completed-item claims are never reusable.
    """
    if not request.terminal or request.unsettled:
        raise RequestError("REQUEST_NOT_SETTLED", "resume open requests; settle outstanding outcomes before retrying")
    if not source_message_id or not new_id or new_id == request.request_id:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "successor requires a new request ID and accepted user source")
    items = tuple(
        replace(item, status=ItemStatus.PENDING, waiting_reasons=(), evidence_ids=()) for item in request.items
    )
    provenance = Evidence(
        f"retry-of:{new_id}",
        "retry_of",
        1,
        (),
        request.request_id,
        content_digest=digest({"source_message_id": source_message_id, "previous_revision": request.revision}),
    )
    return UserRequest(
        new_id,
        request.session_id,
        request.goal,
        items,
        scope=request.scope,
        constraints=request.constraints,
        source_message_ids=(source_message_id,),
        work_round_id=source_message_id,
        related_to=request.request_id,
        evidence=(provenance,),
    )


def clarify_directive(
    requests: Sequence[UserRequest],
    batch: RoutingBatch,
    local_id: str,
    target_request_id: str,
    source: RoutingSource,
) -> RoutingResult:
    """Resolve one saved ambiguity without rewriting or replaying accepted items.

    The original proposal/digest remains immutable. The new accepted source and
    receipt diagnostic retain correction provenance. The caller commits this
    batch and its requests together, then consumes the clarification ingress.
    """
    receipt = next((item for item in batch.receipts if item.local_id == local_id), None)
    if receipt is None or receipt.status != "needs_clarification" or batch.proposal is None:
        raise RequestError("REQUEST_CLARIFICATION_INVALID", "only a saved unresolved directive may be clarified")
    directive = next((d for d in batch.proposal.directives if d["local_id"] == local_id), None)
    if directive is None or directive["action"] == "CREATE":
        raise RequestError("REQUEST_CLARIFICATION_INVALID", "this directive cannot be resolved by choosing a target")
    target = next((request for request in requests if request.request_id == target_request_id), None)
    if target is None:
        raise RequestError("REQUEST_TARGET_AMBIGUOUS", "the selected request does not exist")
    if target.session_id != batch.session_id or target.scope != source.scope:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "clarification target is outside the accepted input scope")
    if source.kind != "user" or not source.message_id or not source.text.strip():
        raise RequestError("REQUEST_PROTOCOL_INVALID", "clarification requires a real accepted user source")
    if any(old.message_id == source.message_id for old in batch.sources):
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "clarification must cite a fresh input message")
    original_source = next((item for item in batch.sources if item.message_id == directive["message_id"]), None)
    if original_source is None or original_source.scope != source.scope:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "clarification cannot change the original directive scope")
    corrected = {
        **directive,
        "message_id": source.message_id,
        "span": [0, len(source.text)],
        "target_id": target.request_id,
        "expected_revision": target.revision,
    }
    proposal = parse_proposal({"protocol_version": 1, "directives": [corrected]})
    isolated = RoutingBatch(batch.batch_id, batch.session_id, (source,))
    result = apply_proposal(tuple(requests), isolated, proposal)
    replacement = result.batch.receipts[0]
    audit = {
        "previous_diagnostic": receipt.diagnostic,
        "clarification_message_id": source.message_id,
        "target_request_id": target.request_id,
        "expected_revision": target.revision,
        "diagnostic": replacement.diagnostic,
    }
    replacement = replace(replacement, diagnostic=json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return RoutingResult(
        result.requests,
        replace(
            batch,
            sources=(*batch.sources, source),
            receipts=tuple(replacement if item.local_id == local_id else item for item in batch.receipts),
        ),
    )


def reassign_unexecuted_item(
    source: UserRequest,
    target: UserRequest,
    item_id: str,
    *,
    source_message_id: str,
    expected_source_revision: int,
    expected_target_revision: int,
) -> tuple[UserRequest, UserRequest]:
    """Move an untouched independent item between compatible untouched requests.

    Both returned requests must be persisted atomically. Once execution, approval
    or answer evidence exists, reassignment needs a new revision/review workflow
    and is deliberately rejected here. Moving the final source item is a merge,
    which this operation does not attempt.
    """
    require_open(source, expected_source_revision)
    require_open(target, expected_target_revision)
    if source.request_id == target.request_id or not source_message_id:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "correction needs distinct requests and its accepted source")
    if (
        source.session_id != target.session_id
        or source.scope != target.scope
        or source.constraints != target.constraints
    ):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "item correction cannot change execution scope or constraints")
    for request in (source, target):
        if (
            request.effects
            or request.dispatches
            or request.evidence
            or request.automatic_turns
            or request.pause_reasons
        ):
            raise RequestError("REQUEST_REASSIGN_UNSAFE", "only untouched requests can be corrected directly")
        if any(i.status != ItemStatus.PENDING or i.waiting_reasons or i.evidence_ids for i in request.items):
            raise RequestError("REQUEST_REASSIGN_UNSAFE", "approval or outcome state requires explicit revalidation")
    item = next((i for i in source.items if i.item_id == item_id), None)
    if item is None or len(source.items) <= 1 or any(i.item_id == item_id for i in target.items):
        raise RequestError("REQUEST_REASSIGN_UNSAFE", "item must exist, remain unique and leave a nonempty source")
    if item.dependencies or any(item_id in other.dependencies for other in source.items):
        raise RequestError("REQUEST_REASSIGN_UNSAFE", "dependent items cannot be reassigned independently")
    event_prefix = f"reassign:{source_message_id}:{source.request_id}:{target.request_id}:{item_id}"
    updated = []
    for request, items, side in (
        (source, tuple(i for i in source.items if i.item_id != item_id), "source"),
        (target, (*target.items, item), "target"),
    ):
        event = RequestEvent(
            f"{event_prefix}:{side}",
            "amend",
            request.revision,
            {"source_message_id": source_message_id, "items": [i.to_dict() for i in items]},
        )
        amended = reduce_request(request, event)
        updated.append(replace(amended, source_message_ids=(*amended.source_message_ids, source_message_id)))
    return updated[0], updated[1]


def recover_session_requests(
    state: Mapping[str, Any],
    live_job_ids: Sequence[str] = (),
    proven_outcomes: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Reconcile requests without discarding unrelated manifest/migration fields."""
    restored = deepcopy(dict(state))
    if "requests" not in restored:
        return restored
    known_fields = {field.name for field in fields(UserRequest)}
    recovered = []
    for record in restored["requests"]:
        known = {key: value for key, value in record.items() if key in known_fields}
        request = UserRequest.from_dict(known)
        updated = recover_request(request, live_job_ids=frozenset(live_job_ids), proven_outcomes=proven_outcomes)
        recovered.append({**record, **updated.to_dict()})
    restored["requests"] = recovered
    return restored


def apply_management_command(
    state: dict,
    *,
    kind: str,
    source: RoutingSource,
    context_data: dict,
    selection: dict,
    payload: dict,
) -> tuple[str, ...]:
    """Apply a real user management input inside the caller's Session transaction."""
    if source.kind != "user" or not source.message_id or not source.text.strip():
        raise RequestError("REQUEST_PROTOCOL_INVALID", "management requires an accepted user action with text")
    command_digest = digest({
        "kind": kind,
        "source": {
            "message_id": source.message_id,
            "text": source.text,
            "scope": source.scope,
        },
        "payload": payload,
        "selection": selection,
    })
    commands = state.setdefault("management_commands", {})
    previous = commands.get(source.message_id)
    if previous:
        if previous["digest"] != command_digest:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "management command ID reused with different content")
        return tuple(previous["request_ids"])
    if any(item["message_id"] == source.message_id for item in state.get("ingress", ())):
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "input already belongs to another admission")
    requests = tuple(UserRequest.from_dict(record) for record in state.get("requests", ()))
    by_id = {request.request_id: request for request in requests}

    def target(request_id):
        request = by_id.get(request_id)
        if request is None:
            raise RequestError("REQUEST_TARGET_AMBIGUOUS", "selected request does not exist")
        if request.session_id != context_data["session_id"] or request.scope != source.scope:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "selected request is outside the accepted user scope")
        return request

    batch_id = ""
    if kind == "clarify":
        batch_id = payload["batch_id"]
        saved = next((b for b in state.get("batches", ()) if b["batch"]["batch_id"] == batch_id), None)
        if saved is None:
            raise RequestError("REQUEST_CLARIFICATION_INVALID", "saved routing batch does not exist")
        target(payload["target_request_id"])
        result = clarify_directive(
            requests, RoutingBatch.from_dict(saved["batch"]), payload["local_id"], payload["target_request_id"], source
        )
        saved.update(
            batch=result.batch.to_dict(),
            status="applied"
            if all(receipt.status == "applied" for receipt in result.batch.receipts)
            else "needs_clarification",
        )
        by_id = {request.request_id: request for request in result.requests}
        request_ids = (payload["target_request_id"],)
    elif kind == "restart":
        old = target(payload["request_id"])
        from uuid import NAMESPACE_URL, uuid5

        new_id = str(uuid5(NAMESPACE_URL, f"{old.session_id}/successor/{source.message_id}"))
        if new_id in by_id:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "successor request identity already exists")
        by_id[new_id] = successor_request(old, source.message_id, new_id)
        request_ids = (new_id,)
    elif kind == "reassign":
        new_source, new_target = reassign_unexecuted_item(
            target(payload["source_request_id"]),
            target(payload["target_request_id"]),
            payload["item_id"],
            source_message_id=source.message_id,
            expected_source_revision=payload["expected_source_revision"],
            expected_target_revision=payload["expected_target_revision"],
        )
        by_id[new_source.request_id], by_id[new_target.request_id] = new_source, new_target
        request_ids = (new_source.request_id, new_target.request_id)
    else:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown management command")
    state["requests"] = [request.to_dict() for request in by_id.values()]
    commands[source.message_id] = {"kind": kind, "digest": command_digest, "request_ids": list(request_ids)}
    ingress = state.setdefault("ingress", [])
    ingress.append({
        "message_id": source.message_id,
        "text": source.text,
        "sequence": len(ingress) + 1,
        "context": deepcopy(context_data),
        "selection": deepcopy(selection),
        "digest": command_digest,
        "status": "applied",
        "batch_id": batch_id,
        "management_kind": kind,
    })
    return request_ids
