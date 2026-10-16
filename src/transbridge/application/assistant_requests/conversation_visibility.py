"""Durable conversation visibility without deleting request execution evidence."""

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .journal import EventCause


def visible_history(records, state):
    hidden = set(state.get("conversation_clear", {}).get("message_ids", ()))
    return [record for record in records if record.get("message_id") not in hidden]


def clear_conversation(service, context, history):
    """Serialize a clear boundary after already queued admissions and preserve receipts."""
    with service.serialized(context):
        service.save_history(context, history)
        snapshot = service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
        identities = {record.get("message_id") for record in snapshot.backend_messages()}
        identities.discard(None)

        def clear(state):
            previous = state.get("conversation_clear", {})
            state["conversation_clear"] = {
                "generation": previous.get("generation", 0) + 1,
                "message_ids": sorted(identities | set(previous.get("message_ids", ()))),
            }
            for item in state.get("ingress", ()):
                if item.get("status") in {"pending", "routing"}:
                    item["status"] = "discarded"
            for entry in state.get("batches", ()):
                unresolved = any(
                    receipt.get("status") in {"needs_clarification", "needs_replan"}
                    for receipt in entry.get("batch", {}).get("receipts", ())
                )
                if unresolved or entry.get("status") not in {"applied", "completed"}:
                    entry["status"] = "discarded"

        state = service.transact(context, clear, history=[], cause=EventCause("conversation.cleared", "user"))
        service.notify(context.session_id)
        return state
