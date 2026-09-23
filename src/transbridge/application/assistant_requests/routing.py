"""Validated routing proposals with durable per-directive replay identities."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .models import ItemStatus, RequestError, RequestItem, UserRequest, digest
from .reducer import RequestEvent, reduce_request


@dataclass(frozen=True, slots=True)
class RoutingSource:
    message_id: str
    text: str
    scope: tuple[tuple[str, str], ...] = ()
    kind: str = "user"


@dataclass(frozen=True, slots=True)
class RoutingProposal:
    protocol_version: int
    directives: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict:
        return {"protocol_version": self.protocol_version, "directives": list(self.directives)}


@dataclass(frozen=True, slots=True)
class DirectiveReceipt:
    local_id: str
    directive_id: str
    status: str
    request_id: str = ""
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class RoutingBatch:
    batch_id: str
    session_id: str
    sources: tuple[RoutingSource, ...]
    proposal: RoutingProposal | None = None
    proposal_digest: str = ""
    receipts: tuple[DirectiveReceipt, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RoutingBatch:
        sources = tuple(
            RoutingSource(**{**s, "scope": tuple(tuple(p) for p in s.get("scope", ()))}) for s in data["sources"]
        )
        return cls(
            data["batch_id"],
            data["session_id"],
            sources,
            parse_proposal(data["proposal"]) if data.get("proposal") else None,
            data.get("proposal_digest", ""),
            tuple(DirectiveReceipt(**r) for r in data.get("receipts", ())),
        )


@dataclass(frozen=True, slots=True)
class RoutingResult:
    requests: tuple[UserRequest, ...]
    batch: RoutingBatch


_ACTIONS = {"RESPOND", "CREATE", "FOLLOW_UP", "AMEND", "PAUSE", "RESUME", "CANCEL", "REPLACE"}
_FIELDS = {
    "local_id",
    "message_id",
    "span",
    "action",
    "goal",
    "items",
    "target_id",
    "expected_revision",
    "constraints",
    "related_to",
    "response",
}


def parse_proposal(data: dict) -> RoutingProposal:
    if set(data) != {"protocol_version", "directives"} or data["protocol_version"] != 1:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown routing protocol fields/version")
    if not isinstance(data["directives"], (list, tuple)) or not data["directives"]:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "routing directives must be a nonempty list")
    seen = set()
    directives = []
    for original in data["directives"]:
        if not isinstance(original, dict):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "directive must be an object")
        directive = dict(original)
        if set(directive) - _FIELDS or not {"local_id", "message_id", "span", "action"} <= set(directive):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown or missing directive fields")
        local_id = directive["local_id"]
        if not isinstance(local_id, str) or not local_id or local_id in seen:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "local directive IDs must be unique")
        seen.add(local_id)
        if directive["action"] not in _ACTIONS:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown directive action")
        if directive["action"] == "RESPOND":
            if (
                set(directive) != {"local_id", "message_id", "span", "action", "response"}
                or not isinstance(directive["response"], str)
                or not directive["response"].strip()
            ):
                raise RequestError("REQUEST_PROTOCOL_INVALID", "direct replies require text and cannot change requests")
        elif "response" in directive:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "response is only valid for RESPOND")
        span = directive["span"]
        if not isinstance(span, (list, tuple)) or len(span) != 2 or any(type(n) is not int for n in span):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "source span must contain two integer offsets")
        if "expected_revision" in directive and type(directive["expected_revision"]) is not int:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "expected revision must be an integer")
        for raw in directive.get("items", ()):
            if set(raw) - {"item_id", "description", "kind", "required", "dependencies"}:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "model cannot supply item states or evidence")
            item = RequestItem.from_dict(raw)
            if item.status != ItemStatus.PENDING:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "new items must be pending")
        directives.append(directive)
    return RoutingProposal(1, tuple(directives))


def seal_proposal(batch: RoutingBatch, proposal: RoutingProposal) -> RoutingBatch:
    """Persist this result before applying directives; retries must reuse it."""
    fingerprint = digest(proposal.to_dict())
    if batch.proposal_digest:
        if fingerprint != batch.proposal_digest:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "routing batch already has a different saved proposal")
        return batch
    sources = {source.message_id: source for source in batch.sources}
    for directive in proposal.directives:
        source = sources.get(directive["message_id"])
        start, end = directive["span"]
        if source is None or source.kind != "user" or not 0 <= start < end <= len(source.text):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "directive must cite an accepted user input span")
    return replace(batch, proposal=proposal, proposal_digest=fingerprint)


def apply_proposal(requests: tuple[UserRequest, ...], batch: RoutingBatch, proposal: RoutingProposal) -> RoutingResult:
    batch = seal_proposal(batch, proposal)
    by_id = {r.request_id: r for r in requests}
    receipts = {r.local_id: r for r in batch.receipts}
    sources = {s.message_id: s for s in batch.sources}
    for directive in proposal.directives:
        local_id = directive["local_id"]
        if local_id in receipts:  # unresolved directives require an explicit clarification command
            continue
        directive_id = str(uuid5(NAMESPACE_URL, f"{batch.session_id}/{batch.batch_id}/directive/{local_id}"))
        request_id = str(uuid5(NAMESPACE_URL, f"{batch.session_id}/{batch.batch_id}/request/{local_id}"))
        action = directive["action"]
        source = sources[directive["message_id"]]
        try:
            if action == "RESPOND":
                receipts[local_id] = DirectiveReceipt(local_id, directive_id, "applied")
                continue
            target_id = str(directive.get("target_id", ""))
            if target_id.startswith("local:"):
                target_receipt = receipts.get(target_id.removeprefix("local:"))
                target_id = target_receipt.request_id if target_receipt and target_receipt.status == "applied" else ""
            target = by_id.get(target_id)
            if action != "CREATE":
                if target is None:
                    raise RequestError(
                        "REQUEST_TARGET_AMBIGUOUS", "select an existing request before applying this action"
                    )
                if target.session_id != batch.session_id or target.scope != source.scope:
                    raise RequestError("REQUEST_SCOPE_MISMATCH", "target is outside the accepted input scope")
                if directive.get("expected_revision") != target.revision:
                    raise RequestError(
                        "REQUEST_REVISION_CONFLICT", "target revision changed; clarify against current state"
                    )
            if action in ("CREATE", "FOLLOW_UP", "REPLACE"):
                items = tuple(RequestItem.from_dict(i) for i in directive.get("items", ()))
                created = UserRequest(
                    request_id,
                    batch.session_id,
                    str(directive.get("goal", "")),
                    items,
                    scope=source.scope,
                    constraints=tuple(directive.get("constraints", ())),
                    source_message_ids=(source.message_id,),
                    work_round_id=source.message_id,
                    related_to=target_id,
                )
                if request_id in by_id and by_id[request_id] != created:
                    raise RequestError("COMMAND_PAYLOAD_CONFLICT", "request identity already exists")
                if action == "REPLACE":
                    assert target is not None
                    by_id[target_id] = reduce_request(
                        target, RequestEvent(directive_id, "replace", target.revision, {"successor_id": request_id})
                    )
                by_id[request_id] = created
            else:
                assert target is not None
                payload = {k: directive[k] for k in ("goal", "items", "constraints") if k in directive}
                payload["source_message_id"] = source.message_id
                by_id[target_id] = reduce_request(
                    target, RequestEvent(directive_id, action.lower(), target.revision, payload)
                )
                request_id = target_id
            receipts[local_id] = DirectiveReceipt(local_id, directive_id, "applied", request_id)
        except (RequestError, ValueError, TypeError) as error:
            receipts[local_id] = DirectiveReceipt(local_id, directive_id, "needs_clarification", diagnostic=str(error))
    return RoutingResult(tuple(by_id.values()), replace(batch, receipts=tuple(receipts.values())))
