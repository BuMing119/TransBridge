"""Canonical routing state reduction and direct-reply projection for result commits."""

from .models import RequestError
from .routing import RoutingBatch, apply_proposal, parse_proposal


def direct_replies(batch):
    """Stable message identities make retries and restored views idempotent."""
    receipts = {r["local_id"]: r for r in batch.get("receipts", ())}
    for directive in (batch.get("proposal") or {}).get("directives", ()):
        receipt = receipts.get(directive["local_id"], {})
        if directive["action"] == "RESPOND" and receipt.get("status") == "applied":
            yield {
                "message_id": "reply-" + receipt["directive_id"],
                "role": "assistant",
                "content": directive["response"],
            }


def apply_routing_state(service, state, batch_id, proposal):
    """Apply the canonical routing reducer inside the caller's Session transaction."""
    batch = next((b for b in state.get("batches", ()) if b["batch"]["batch_id"] == batch_id), None)
    if state.get("session_tombstone"):
        raise RequestError("SESSION_DELETING", "会话正在删除，不能应用迟到的路由。")
    if batch is None:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown routing batch")
    result = apply_proposal(service.requests(state), RoutingBatch.from_dict(batch["batch"]), parse_proposal(proposal))
    state["requests"] = [r.to_dict() for r in result.requests]
    batch.update(batch=result.batch.to_dict(), status="applied")
    for item in state.get("ingress", ()):
        if item["batch_id"] == batch_id:
            item["status"] = "applied"
    return batch["batch"]
