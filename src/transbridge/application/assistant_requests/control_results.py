"""Durable terminal receipts for admitted read and answer control calls.

These use cases run on a worker. Qt owns presentation, never receipt arbitration.
The service, scheduler and Session locks are acquired in that order.
"""

from copy import deepcopy
import json
from uuid import uuid4

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .admission import validate_turn
from .models import RequestError
from .summary_service import RequestSummaryService

CONTROL_RESULT_TOOLS = frozenset({
    "read_request_state",
    "read_request_history",
    "read_request_result",
    "report_answer_coverage",
})


def _request(service, context, admission, state):
    request = next((r for r in service.requests(state) if r.request_id == admission.request_id), None)
    if (
        request is None
        or state.get("session_tombstone")
        or request.session_id != context.session_id
        or context.session_id != admission.session_id
        or request.scope != admission.scope
        or any(context.to_dict().get(key) != value for key, value in request.scope)
    ):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "control receipt is outside its request scope")
    return request


def _call(service, context, state, request_id, call_id, tool_name):
    if not call_id or tool_name not in CONTROL_RESULT_TOOLS:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "unsupported control result identity")
    snapshot = service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    history = RequestSummaryService(service)._history(snapshot, state)
    parents = [
        (message, call)
        for message in history
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", ())
        if call.get("id") == call_id
    ]
    if len(parents) != 1:
        raise RequestError("CONTROL_PARENT_MISSING", "control call requires one persisted assistant parent")
    parent, call = parents[0]
    if call.get("name") != tool_name:
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "control call has another tool name")
    owner = state.get("message_owners", {}).get(parent["message_id"], parent.get("request_id"))
    if owner != request_id:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "control parent belongs to another request")
    receipts = [m for m in history if m.get("role") == "tool" and m.get("tool_call_id") == call_id]
    if len(receipts) > 1:
        raise RequestError("CONTROL_RESULT_CONFLICT", "control call already has multiple terminal receipts")
    receipt = (
        {
            key: receipts[0][key]
            for key in ("role", "tool_call_id", "name", "content", "display_summary", "is_error", "message_id")
            if key in receipts[0]
        }
        if receipts
        else None
    )
    return parent, receipt


def _receipt(call_id, tool_name, result, is_error):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": tool_name,
        "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
        "display_summary": "",
        "is_error": is_error,
        "message_id": uuid4().hex,
    }


def _record(state, receipt, request_id):
    identity = receipt["message_id"]
    state.setdefault("message_owners", {})[identity] = request_id
    state.setdefault("result_owners", {})[identity] = request_id
    state["recorded_ids"] = list(dict.fromkeys((*state.get("recorded_ids", ()), identity)))


def commit_control_result(
    service, context, admission, tool_call_id, tool_name, result, *, is_error=False, change=None, cancelled=None
):
    """Save exactly one receipt, optionally with a request mutation.

    ``result`` may be a pure zero-argument local read, evaluated under the
    admission fence. ``change(request, parent)`` returns the new request.
    A repeated identical result is idempotent; a conflicting result fails.
    """
    additions, published = [], []

    def apply(state):
        additions.clear()
        published.clear()
        request = _request(service, context, admission, state)
        parent, existing = _call(service, context, state, request.request_id, tool_call_id, tool_name)
        # A read retry after completion returns its already-committed page; do
        # not read today's data and pretend the original call saw that data.
        if existing is not None and callable(result):
            published.append(existing)
            return
        if existing is None:
            if parent.get("control_turn_id") != admission.turn_id:
                raise RequestError("TURN_LEASE_STALE", "control parent was not admitted in this turn")
            validate_turn(request, admission, tool_name=tool_name, scheduler=service.scheduler)
            if cancelled is not None and cancelled():
                raise RequestError("CONTROL_CANCELLED", "control call was interrupted before reading")
        value = result() if callable(result) else result
        receipt = _receipt(tool_call_id, tool_name, value, is_error)
        if existing is not None:
            if (
                json.loads(existing["content"]) != value
                or existing.get("name") != tool_name
                or bool(existing.get("is_error")) != is_error
            ):
                raise RequestError("CONTROL_RESULT_CONFLICT", "control call already finished with a different result")
            published.append(existing)
            return
        if cancelled is not None and cancelled():
            raise RequestError("CONTROL_CANCELLED", "control call was interrupted before committing")
        if change is not None:
            updated = change(request, parent)
            state["requests"] = [
                updated.to_dict() if r["request_id"] == request.request_id else r for r in state["requests"]
            ]
        _record(state, receipt, request.request_id)
        additions.append(receipt)
        published.append(receipt)

    with service.serialized(context), service.scheduler.serialized():
        service.transact(context, apply, append_messages=additions)
    return deepcopy(published[0])


def finish_control_calls(service, context, admission, calls, *, reason="会话中断，工具调用已取消。"):
    """Finish only explicitly owned control calls, retaining any prior success.

    Admission may already have been revoked. This operation grants no execution
    permission and does not cancel business effects. Callers pass (id, name) pairs.
    """
    calls = tuple(calls)
    if len({identity for identity, _ in calls}) != len(calls):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "duplicate cancellation call identity")
    additions, published = [], []

    def apply(state):
        additions.clear()
        published.clear()
        request = _request(service, context, admission, state)
        for identity, name in calls:
            _, existing = _call(service, context, state, request.request_id, identity, name)
            receipt = existing or _receipt(identity, name, {"error": reason}, True)
            if existing is None:
                _record(state, receipt, request.request_id)
                additions.append(receipt)
            published.append(receipt)

    with service.serialized(context), service.scheduler.serialized():
        service.transact(context, apply, append_messages=additions)
        service.scheduler.release(admission)
    return deepcopy(published)
