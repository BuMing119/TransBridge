"""Atomic routing decisions and terminal model receipts, independent of Qt."""

from copy import deepcopy
import json
from uuid import uuid4

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .journal import EventCause
from .models import RequestError, digest
from .routing_commit import apply_routing_state, direct_replies
from .summary_service import RequestSummaryService

ROUTING_TOOL = "submit_request_routing"


def _parent_result(service, context, state, parent, identity):
    snapshot = service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    history = RequestSummaryService(service)._history(snapshot, state)
    parents = [
        (message, call)
        for message in history
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", ())
        if call.get("id") == identity
    ]
    if len(parents) != 1:
        raise RequestError("CONTROL_PARENT_MISSING", "routing needs one persisted parent call")
    saved, call = parents[0]
    if (
        call.get("name") != ROUTING_TOOL
        or saved["message_id"] != parent["message_id"]
        or saved.get("control_batch_id") != parent.get("control_batch_id")
        or saved.get("control_turn_id") != parent.get("control_turn_id")
    ):
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "routing parent identity changed")
    receipts = [
        message for message in history if message.get("role") == "tool" and message.get("tool_call_id") == identity
    ]
    if len(receipts) > 1:
        raise RequestError("CONTROL_RESULT_CONFLICT", "routing call has multiple terminal receipts")
    existing = (
        {
            key: receipts[0][key]
            for key in ("role", "tool_call_id", "name", "content", "message_id", "is_error", "display_summary")
            if key in receipts[0]
        }
        if receipts
        else None
    )
    return call, existing


def _receipt(identity, content, *, is_error=False):
    return {
        "role": "tool",
        "tool_call_id": identity,
        "name": ROUTING_TOOL,
        "content": json.dumps(content, ensure_ascii=False, sort_keys=True),
        "message_id": uuid4().hex,
        "is_error": is_error,
        "display_summary": "",
    }


def finish_routing_call(service, context, parent, identity, *, reason):
    additions, output = [], []

    def finish(state):
        additions.clear()
        output.clear()
        _, existing = _parent_result(service, context, state, parent, identity)
        receipt = existing or _receipt(identity, {"error": reason}, is_error=True)
        if existing is None:
            additions.append(receipt)
        output.append(receipt)

    with service.serialized(context):
        service.transact(context, finish, append_messages=additions)
    return deepcopy(output[0])


def run_routing_call(service, context, admission, batch_id, parsed, parent, *, cancelled, history=None):
    """Publish decision, direct answers and tool receipt as one durable result."""
    identity = parsed["tool_call_id"]
    service.save_history(context, [parent] if history is None else history)
    additions, output = [], []

    def apply(state):
        additions.clear()
        output.clear()
        call, existing = _parent_result(service, context, state, parent, identity)
        if call.get("arguments") != parsed["arguments"]:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "routing arguments differ from the saved call")
        if existing is not None:
            output.append(existing)
            return
        service.scheduler.validate(admission)
        if (
            admission.stage != "routing"
            or admission.session_id != context.session_id
            or parent.get("control_batch_id") != batch_id
            or parent.get("control_turn_id") != admission.turn_id
        ):
            raise RequestError("TURN_LEASE_STALE", "routing belongs to another turn or batch")
        if cancelled():
            raise RequestError("CONTROL_CANCELLED", "routing was interrupted before commit")
        batch = apply_routing_state(service, state, batch_id, parsed["arguments"])
        if cancelled():
            raise RequestError("CONTROL_CANCELLED", "routing was interrupted before commit")
        receipt = _receipt(identity, batch)
        additions.extend((receipt, *direct_replies(batch)))
        output.append(receipt)

    try:
        with service.serialized(context), service.scheduler.serialized():
            service.transact(
                context,
                apply,
                append_messages=additions,
                cause=EventCause(
                    "routing.applied", "model", {"batch_id": batch_id}, {"proposal_digest": digest(parsed["arguments"])}
                ),
            )
    except RequestError as exc:
        if exc.code not in {
            "CONTROL_CANCELLED",
            "TURN_LEASE_STALE",
            "REQUEST_PROTOCOL_INVALID",
            "REQUEST_REVISION_CONFLICT",
        }:
            raise
        return finish_routing_call(service, context, parent, identity, reason=str(exc))
    service.notify(context.session_id)
    return deepcopy(output[0])
