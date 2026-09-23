"""Local control-query/answer use cases, including interrupted-call recovery."""

from types import SimpleNamespace

from transbridge.application.assistant_context.history_queries import read_request_history
from transbridge.application.assistant_context.state_queries import read_request_state
from transbridge.persistence.v2.ids import SessionId, SessionRef

from .control_results import CONTROL_RESULT_TOOLS, commit_control_result, finish_control_calls
from .models import RequestError
from .scheduler import commit_answer
from .summary_service import RequestSummaryService


def run_control_call(service, context, admission, parsed, parent, *, stop_reason=None, cancelled=None):
    """Persist the parent first, then read/commit or explicitly finish a rejection."""
    identity, name = parsed["tool_call_id"], parsed["control"]
    arguments = parsed["arguments"]
    service.save_history(context, [parent], request_id=admission.request_id)

    def read():
        if name == "read_request_state":
            return read_request_state(service, context, admission, **arguments)
        if name == "read_request_history":
            return read_request_history(service, context, admission.request_id, **arguments)
        if name == "read_request_result":
            return service.read_result(context, admission.request_id, **arguments)
        raise RequestError("REQUEST_PROTOCOL_INVALID", "unsupported control query")

    change = None
    result = read
    if name == "report_answer_coverage":
        if set(arguments) != {"item_ids", "dispositions"} or set(arguments["item_ids"]) != set(
            arguments["dispositions"]
        ):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "answer coverage fields disagree")
        result = {"accepted": True}

        def change(request, saved_parent):
            return commit_answer(
                request,
                admission,
                saved_parent["content"],
                arguments["dispositions"],
                message_id=saved_parent["message_id"],
                finish_reason=stop_reason,
                scheduler=service.scheduler,
            )

    try:
        return commit_control_result(
            service,
            context,
            admission,
            identity,
            name,
            result,
            change=change,
            cancelled=cancelled,
        )
    except RequestError as exc:
        if exc.code not in {
            "CONTROL_CANCELLED",
            "TURN_LEASE_STALE",
            "REQUEST_STATE_CHANGED",
            "REQUEST_PAUSED",
            "ANSWER_INCOMPLETE",
            "REQUEST_PROTOCOL_INVALID",
            "ARTIFACT_UNAVAILABLE",
        }:
            raise
        # One terminal error is saved. No retry, re-query, or fallback authority.
        return finish_control_calls(service, context, admission, [(identity, name)], reason=str(exc))[0]


def recover_control_calls(service, context):
    """Close persisted owned calls left unfinished by a previous process/view."""
    with service.serialized(context):
        state = service.state(context)
        snapshot = service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
        history = RequestSummaryService(service)._history(snapshot, state)
        finished = {m["tool_call_id"] for m in history if m.get("role") == "tool"}
        requests = {r.request_id: r for r in service.requests(state)}
        receipts = []
        for parent in history:
            if parent.get("role") != "assistant" or not parent.get("control_turn_id"):
                continue
            if parent.get("control_batch_id"):
                from .routing_results import ROUTING_TOOL, finish_routing_call

                for call in parent.get("tool_calls", ()):
                    if call.get("name") == ROUTING_TOOL and call["id"] not in finished:
                        receipts.append(
                            finish_routing_call(
                                service, context, parent, call["id"], reason="会话恢复时取消了未完成的请求路由。"
                            )
                        )
            owner = state.get("message_owners", {}).get(parent["message_id"])
            calls = [
                (call["id"], call["name"])
                for call in parent.get("tool_calls", ())
                if call["name"] in CONTROL_RESULT_TOOLS and call["id"] not in finished
            ]
            if not calls:
                continue
            if owner not in requests:
                raise RequestError("REQUEST_SCOPE_MISMATCH", "unfinished control parent has no request owner")
            request = requests[owner]
            old = SimpleNamespace(
                request_id=owner, session_id=context.session_id, scope=request.scope, turn_id=parent["control_turn_id"]
            )
            receipts.extend(
                finish_control_calls(service, context, old, calls, reason="会话恢复时取消了未完成的控制查询。")
            )
        return receipts
