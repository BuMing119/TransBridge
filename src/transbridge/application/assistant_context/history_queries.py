"""Bounded reads of authoritative request history; retrieved text is material only."""

from __future__ import annotations

import hashlib
import json

from transbridge.application.assistant_requests.archival import hydrate_request_state
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.summary_service import RequestSummaryService
from transbridge.persistence.v2.ids import SessionId, SessionRef
from transbridge.persistence.v2.models import BackupVerificationError


def validate_history_source(state, request, message_id):
    """Use current authority, never ownership metadata frozen into an old transcript."""
    owners = {**state.get("result_owners", {}), **state.get("message_owners", {})}
    if message_id in owners:
        allowed = owners[message_id] == request.request_id
    else:
        sources = {*request.source_message_ids, *(revision.source_message_id for revision in request.revisions)}
        sources.update(e.reference for e in request.evidence if e.kind == "answer")
        allowed = message_id in sources
    if not allowed:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "history source does not belong to this request")


def read_request_history(service, context, request_id, message_id, offset=0, limit=2000):
    """Read one character page by exact source identity, without changing request state."""
    if (
        not isinstance(message_id, str)
        or not message_id
        or type(offset) is not int
        or type(limit) is not int
        or offset < 0
        or not 1 <= limit <= 8000
    ):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid history source or range")
    if not context.session_id:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "a saved Session is required")
    snapshot = service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    state = snapshot.assistant_data()
    if state.get("request_archives"):
        state = hydrate_request_state(state, context.session_id, service.transcript_store)
    request = next((r for r in service.requests(state) if r.request_id == request_id), None)
    if (
        request is None
        or request.session_id != context.session_id
        or any(context.to_dict().get(key) != value for key, value in request.scope)
        or state.get("session_tombstone")
    ):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "history request is outside the current scope")
    validate_history_source(state, request, message_id)
    try:
        history = RequestSummaryService(service)._history(snapshot, state)
    except (BackupVerificationError, FileNotFoundError) as exc:
        raise RequestError("ARTIFACT_UNAVAILABLE", "original history is missing or cannot be verified") from exc
    message = next((m for m in history if m.get("message_id") == message_id), None)
    if message is None:
        raise RequestError("ARTIFACT_UNAVAILABLE", "the original history source is unavailable")
    if message.get("role") not in {"user", "assistant"}:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "use read_request_result for a tool result")
    content = message.get("content", "")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
    if offset > len(text):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "history offset exceeds the original length")
    end = min(offset + limit, len(text))
    return {
        "source_id": message_id,
        "message_id": message_id,
        "role": message["role"],
        "sequence": message.get("sequence"),
        "offset": offset,
        "end": end,
        "limit": limit,
        "total": len(text),
        "digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text[offset:end],
        "material_only": True,
    }
