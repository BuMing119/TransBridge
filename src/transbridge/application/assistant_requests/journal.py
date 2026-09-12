"""Committed lifecycle evidence, independent of scheduling and command replay."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from typing import Any
from uuid import uuid4

from .models import RequestError, digest


@dataclass(frozen=True)
class EventCause:
    operation: str = "request.updated"
    origin: str = "runtime"
    references: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    record_unchanged: bool = False


def _fields(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _model_identifier(value):
    # Local/item/step IDs can contain arbitrary model text, including quoted
    # source content. A stable digest preserves joins without duplicating it.
    return "label:" + digest(value)


def _diagnostic_code(value):
    if not value:
        return ""
    if value.startswith("{"):
        try:
            audit = json.loads(value)
        except ValueError:
            return "REQUEST_PROTOCOL_INVALID"
        value = audit.get("diagnostic", "") if isinstance(audit, dict) else ""
    code = value.split(":", 1)[0]
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) else ("REQUEST_PROTOCOL_INVALID" if value else "")


def _request(value):
    result = _fields(
        value, ("status", "revision", "pause_reasons", "stop_target", "stop_reason", "automatic_turns", "lease_epoch")
    )
    result["intent_digest"] = digest({key: value.get(key) for key in ("goal", "constraints")})
    for name, identity, keys in (
        ("items", "item_id", ("status", "waiting_reasons", "evidence_ids")),
        ("effects", "effect_id", ("status", "job_id", "run_id", "last_sequence", "result_complete")),
        ("dispatches", "dispatch_id", ("status", "job_id", "run_id", "completed_steps", "failed_steps")),
    ):
        result[name] = {}
        for entry in value.get(name, ()):
            fields = _fields(entry, keys)
            for key in ("completed_steps", "failed_steps"):
                if key in fields:
                    fields[key] = [_model_identifier(step) for step in fields[key]]
            result[name][_model_identifier(entry[identity]) if name == "items" else entry[identity]] = fields
    result["evidence_ids"] = [entry["evidence_id"] for entry in value.get("evidence", ())]
    return result


def _batch(entry):
    batch = entry["batch"]
    return {
        "status": entry.get("status"),
        "proposal_digest": batch.get("proposal_digest", ""),
        "directives": [
            {
                **_fields(directive, ("message_id", "span", "action", "expected_revision")),
                "local_id": _model_identifier(directive["local_id"]),
                "target_digest": digest(directive.get("target_id", "")),
            }
            for directive in (batch.get("proposal") or {}).get("directives", ())
        ],
        "receipts": [
            {
                **_fields(receipt, ("directive_id", "status", "request_id")),
                "local_id": _model_identifier(receipt["local_id"]),
                "code": _diagnostic_code(receipt.get("diagnostic", "")),
            }
            for receipt in batch.get("receipts", ())
        ],
    }


def observe_state(state: dict) -> dict:
    """Capture only diagnostic fields before an in-place command mutates state."""
    projection = {
        "requests": {value["request_id"]: _request(value) for value in state.get("requests", ())},
        "ingress": {
            entry["message_id"]: _fields(entry, ("status", "batch_id", "management_kind"))
            for entry in state.get("ingress", ())
        },
        "batches": {entry["batch"]["batch_id"]: _batch(entry) for entry in state.get("batches", ())},
        # Confirmation payloads contain tool arguments; record identity and changes only.
        "confirmations": {key: digest(value) for key, value in state.get("confirmations", {}).items()},
        "session_tombstone": bool(state.get("session_tombstone")),
    }
    # Dataclass reductions use tuples/StrEnum; persisted JSON uses lists/str.
    # Compare their durable representation so a replay does not invent changes.
    return json.loads(json.dumps(projection, ensure_ascii=False))


def _changes(before, after):
    keys = dict.fromkeys((*before, *after))
    changed = [key for key in keys if before.get(key) != after.get(key)]
    return (
        {key: before[key] for key in changed if key in before},
        {key: after[key] for key in changed if key in after},
    )


def _request_references(value, old, new):
    references = {"message_ids": list(value.get("source_message_ids", ()))}
    for collection, identity in (("effects", "effect_id"), ("dispatches", "dispatch_id")):
        changed = {key for key in new.get(collection, {}) if old.get(collection, {}).get(key) != new[collection][key]}
        records = [entry for entry in value.get(collection, ()) if entry[identity] in changed]
        if not records:
            continue
        references[identity + "s"] = [entry[identity] for entry in records]
        for key in ("job_id", "run_id"):
            references.setdefault(key + "s", []).extend(entry[key] for entry in records if entry.get(key))
        for key in ("turn_id", "attempt_id"):
            references.setdefault(key + "s", []).extend(
                entry["execution"][key] for entry in records if entry.get("execution", {}).get(key)
            )
    return references


def append_events(state, before, session_id, cause: EventCause | None = None) -> None:
    """Stage events in the same candidate Session snapshot as the state changes.

    A failed publication discards both. CAS retries recalculate from the newly
    loaded snapshot, so event sequence and competing evidence remain intact.
    """
    cause = cause or EventCause()
    after = observe_state(state)
    pending = []

    def add(operation, old, new, request_ids=(), references=None):
        old, new = _changes(old, new)
        references = {**(references or {}), **cause.references}
        if "item_ids" in references:
            references["item_ids"] = [_model_identifier(item) for item in references["item_ids"]]
        pending.append({
            "operation": operation,
            "origin": cause.origin,
            "request_ids": list(dict.fromkeys((*request_ids, *cause.references.get("request_ids", ())))),
            "before": old,
            "after": new,
            "references": references,
            "details": deepcopy(cause.details),
        })

    for request in state.get("requests", ()):
        identity = request["request_id"]
        old = before["requests"].get(identity, {})
        new = after["requests"][identity]
        if old != new:
            operation = (
                cause.operation
                if cause.operation != "request.updated"
                else ("request.created" if not old else "request.updated")
            )
            add(operation, old, new, (identity,), _request_references(request, old, new))
    for entry in state.get("ingress", ()):
        identity = entry["message_id"]
        old = before["ingress"].get(identity, {})
        new = after["ingress"][identity]
        if old != new:
            operation = (
                "input.accepted" if not old else ("input.routed" if new.get("status") == "applied" else "input.updated")
            )
            add(operation, old, new, references={"message_ids": [identity]})
    for entry in state.get("batches", ()):
        batch = entry["batch"]
        identity = batch["batch_id"]
        old = before["batches"].get(identity, {})
        new = after["batches"][identity]
        if old != new:
            operation = "routing.applied" if new["status"] == "applied" else "routing.prepared"
            if new["status"] == "user_paused":
                operation = "routing.paused"
            elif new["status"] == "cancelled":
                operation = "routing.cancelled"
            elif new["status"] == "needs_clarification":
                operation = "routing.needs_clarification"
            elif cause.operation == "routing.resumed":
                operation = cause.operation
            add(
                operation,
                old,
                new,
                (r["request_id"] for r in batch.get("receipts", ()) if r.get("request_id")),
                {"batch_id": identity, "message_ids": [source["message_id"] for source in batch.get("sources", ())]},
            )
    for key in dict.fromkeys((*before["confirmations"], *after["confirmations"])):
        old = before["confirmations"].get(key)
        new = after["confirmations"].get(key)
        if old != new:
            add("confirmation.changed", {"digest": old}, {"digest": new}, (key,))
    if before["session_tombstone"] != after["session_tombstone"]:
        add("session.deletion", {"deleting": before["session_tombstone"]}, {"deleting": after["session_tombstone"]})
    if not pending and cause.record_unchanged:
        turn_id = cause.references.get("turn_id")
        already_recorded = turn_id and any(
            event["operation"] == cause.operation and event["references"].get("turn_id") == turn_id
            for event in state.get("lifecycle_events", ())
        )
        if not already_recorded:
            add(cause.operation, {}, {}, cause.references.get("request_ids", ()))
    if not pending:
        return
    events = state.setdefault("lifecycle_events", [])
    sequence = events[-1]["sequence"] if events else 0
    timestamp = datetime.now(UTC).isoformat()
    for event in pending:
        sequence += 1
        events.append({
            "event_id": uuid4().hex,
            "sequence": sequence,
            "timestamp": timestamp,
            "session_id": session_id,
            **event,
        })


def read_events(state: dict, *, request_id=None, after_sequence=0, limit=100) -> dict:
    """Read a cursor page from already owner-checked Session state."""
    if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid lifecycle event cursor or page size")
    source_ids = set()
    if request_id is not None:
        request = next((r for r in state.get("requests", ()) if r["request_id"] == request_id), None)
        if request is None:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "request is not in this Session")
        source_ids.update(request.get("source_message_ids", ()))
    selected = []
    for event in state.get("lifecycle_events", ()):
        if event["sequence"] <= after_sequence:
            continue
        if (
            request_id is not None
            and request_id not in event["request_ids"]
            and not source_ids.intersection(event["references"].get("message_ids", ()))
        ):
            continue
        selected.append(event)
        if len(selected) > limit:
            break
    page = selected[:limit]
    return {
        "events": deepcopy(page),
        "next_sequence": page[-1]["sequence"] if page else after_sequence,
        "has_more": len(selected) > limit,
    }
