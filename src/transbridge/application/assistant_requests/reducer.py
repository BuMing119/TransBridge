"""Pure command reduction. Callers persist returned state before acknowledging it."""

from dataclasses import dataclass, field, replace
from typing import Any

from .models import (
    Evidence,
    ItemKind,
    ItemStatus,
    RequestError,
    RequestItem,
    RequestRevision,
    RequestStatus,
    UserRequest,
    digest,
)


@dataclass(frozen=True, slots=True)
class RequestEvent:
    event_id: str
    kind: str
    expected_revision: int
    payload: dict[str, Any] = field(default_factory=dict)


def require_open(request: UserRequest, revision: int) -> None:
    if request.revision != revision:
        raise RequestError("REQUEST_REVISION_CONFLICT", "reload the current request revision")
    if request.status != RequestStatus.OPEN:
        raise RequestError("REQUEST_TERMINAL", "stopped requests cannot accept new work; create a successor")


def converge(request: UserRequest) -> UserRequest:
    """Only evidence-backed, fully settled requests can reach any terminal state."""
    if request.terminal or request.unsettled:
        return request
    if request.status == RequestStatus.OPEN:
        request = _fail_dependants(request)
    if request.status == RequestStatus.STOPPING:
        return replace(
            request,
            status=request.stop_target or RequestStatus.CANCELLED,
            items=tuple(
                replace(i, status=ItemStatus.CANCELLED)
                if i.status not in (ItemStatus.SATISFIED, ItemStatus.FAILED)
                else i
                for i in request.items
            ),
        )
    required = [i for i in request.items if i.required]
    if required and all(i.status == ItemStatus.SATISFIED for i in required):
        return replace(request, status=RequestStatus.COMPLETED)
    if any(i.status == ItemStatus.FAILED for i in required) and all(
        i.status in (ItemStatus.SATISFIED, ItemStatus.FAILED, ItemStatus.CANCELLED) for i in required
    ):
        return replace(request, status=RequestStatus.FAILED)
    return request


def _fail_dependants(request: UserRequest) -> UserRequest:
    """Settle unstarted descendants without touching live or unknown operations."""
    items = {item.item_id: item for item in request.items}
    while True:
        changed = False
        for item in tuple(items.values()):
            if item.status not in {ItemStatus.PENDING, ItemStatus.WAITING}:
                continue
            failed = [
                dep for dep in item.dependencies if items[dep].status in {ItemStatus.FAILED, ItemStatus.CANCELLED}
            ]
            if failed:
                items[item.item_id] = replace(
                    item,
                    status=ItemStatus.FAILED,
                    waiting_reasons=tuple(f"dependency_failed:{dep}" for dep in failed),
                )
                changed = True
        if not changed:
            return replace(request, items=tuple(items.values()))


def add_evidence(request: UserRequest, evidence: Evidence, *, satisfy_items: bool = True) -> UserRequest:
    existing = next((e for e in request.evidence if e.evidence_id == evidence.evidence_id), None)
    if existing:
        if existing != evidence:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "evidence ID reused with different content")
        return request
    updated = replace(request, evidence=(*request.evidence, evidence))
    # Old-revision and late terminal results remain audit evidence only.
    if request.terminal or evidence.request_revision != request.revision or not evidence.complete or not satisfy_items:
        return updated
    if request.status != RequestStatus.OPEN:
        return converge(updated)
    ids = set(evidence.item_ids)
    if not ids or not ids <= {i.item_id for i in request.items} or not evidence.reference:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "evidence requires existing items and saved reference")
    for item in request.items:
        if item.item_id in ids and evidence.kind != ("answer" if item.kind == ItemKind.ANSWER else "execution"):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "answer evidence cannot complete an execution item")
    return converge(
        replace(
            updated,
            items=tuple(
                replace(
                    item,
                    status=ItemStatus.SATISFIED,
                    waiting_reasons=(),
                    evidence_ids=(*item.evidence_ids, evidence.evidence_id),
                )
                if item.item_id in ids
                else item
                for item in updated.items
            ),
        )
    )


def reduce_request(request: UserRequest, event: RequestEvent) -> UserRequest:
    fingerprint = digest({"kind": event.kind, "revision": event.expected_revision, "payload": event.payload})
    prior = dict(request.applied_events).get(event.event_id)
    if prior:
        if prior != fingerprint:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "event ID reused with different content")
        return request
    if not event.event_id:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "event ID is required")
    require_open(request, event.expected_revision)
    data = event.payload
    updated = request
    if event.kind in ("pause", "interrupt"):
        reason = str(data.get("reason", "user_interrupted" if event.kind == "interrupt" else "user_paused"))
        updated = replace(
            request,
            pause_reasons=tuple(dict.fromkeys((*request.pause_reasons, reason))),
        )
    elif event.kind == "resume":
        updated = replace(
            request,
            pause_reasons=tuple(r for r in request.pause_reasons if r not in ("user_paused", "user_interrupted")),
            automatic_turns=0,
            items=tuple(
                replace(
                    item,
                    waiting_reasons=tuple(r for r in item.waiting_reasons if r != "reconciled_result"),
                    status=ItemStatus.PENDING
                    if item.status == ItemStatus.WAITING and item.waiting_reasons == ("reconciled_result",)
                    else item.status,
                )
                for item in request.items
            ),
        )
        updated = converge(updated)
    elif event.kind in ("cancel", "replace", "stop_failure"):
        target = {
            "cancel": RequestStatus.CANCELLED,
            "replace": RequestStatus.SUPERSEDED,
            "stop_failure": RequestStatus.FAILED,
        }[event.kind]
        if event.kind == "replace" and not data.get("successor_id"):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "replacement requires a successor")
        updated = converge(
            replace(
                request,
                status=RequestStatus.STOPPING,
                stop_target=target,
                stop_reason=str(data.get("reason", event.kind)),
                successor_id=str(data.get("successor_id", "")),
                lease_epoch=request.lease_epoch + 1,
                dispatches=tuple(
                    replace(d, status="cancelled") if not d.job_id and d.status == "running" else d
                    for d in request.dispatches
                ),
            )
        )
    elif event.kind == "amend":
        if not data.get("source_message_id"):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "amendment requires its source message")
        old = RequestRevision(
            request.revision, request.goal, request.constraints, request.items, str(data["source_message_id"])
        )
        items = (
            tuple(RequestItem.from_dict(i) for i in data["items"])
            if "items" in data
            else tuple(
                replace(i, status=ItemStatus.PENDING, evidence_ids=(), waiting_reasons=()) for i in request.items
            )
        )
        # Carrying completed results needs explicit revalidation, never model-supplied status.
        if any(i.status != ItemStatus.PENDING or i.evidence_ids for i in items):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "amended items must be pending until revalidated")
        updated = replace(
            request,
            revision=request.revision + 1,
            goal=str(data.get("goal", request.goal)),
            constraints=tuple(data.get("constraints", request.constraints)),
            items=items,
            revisions=(*request.revisions, old),
            dispatches=tuple(
                replace(d, status="cancelled") if not d.job_id and d.status == "running" else d
                for d in request.dispatches
            ),
            lease_epoch=request.lease_epoch + 1,
        )
    elif event.kind in ("wait", "unblock", "fail"):
        ids = set(data.get("item_ids", ()))
        if not ids or not ids <= {i.item_id for i in request.items}:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown or empty item set")
        reason = str(data.get("reason", "needs_clarification"))
        items = []
        for item in request.items:
            if item.item_id not in ids or item.status in (ItemStatus.SATISFIED, ItemStatus.CANCELLED):
                items.append(item)
                continue
            if event.kind == "unblock" and reason not in item.waiting_reasons:
                items.append(item)
                continue
            reasons = (
                tuple(r for r in item.waiting_reasons if r != reason)
                if event.kind == "unblock"
                else tuple(dict.fromkeys((*item.waiting_reasons, reason)))
            )
            status = (
                ItemStatus.FAILED if event.kind == "fail" else (ItemStatus.WAITING if reasons else ItemStatus.PENDING)
            )
            items.append(replace(item, status=status, waiting_reasons=reasons))
        updated = converge(replace(request, items=tuple(items)))
    else:
        raise RequestError("REQUEST_PROTOCOL_INVALID", f"unsupported request command: {event.kind}")
    if event.kind in ("amend", "continue", "resume") and data.get("source_message_id"):
        source_message_id = str(data["source_message_id"])
        updated = replace(
            updated,
            work_round_id=source_message_id,
            source_message_ids=tuple(dict.fromkeys((*updated.source_message_ids, source_message_id))),
        )
    return replace(updated, applied_events=(*updated.applied_events, (event.event_id, fingerprint)))
