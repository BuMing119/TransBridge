"""Read-only, admission-scoped pages of execution details omitted from model input."""

from dataclasses import asdict
import hashlib
import json

from transbridge.application.assistant_requests.admission import validate_turn
from transbridge.application.assistant_requests.models import RequestError

STATE_SECTIONS = ("items", "effects", "evidence", "dispatches", "selection")
STATE_RETRIEVAL_TOOL = "read_request_state"


def state_section(service, context, admission, section):
    """Read current authority; callers cannot choose another request or filesystem path."""
    if section not in STATE_SECTIONS:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown request state section")
    state = service.state(context)
    request = next((r for r in service.requests(state) if r.request_id == admission.request_id), None)
    if (
        request is None
        or state.get("session_tombstone")
        or context.session_id != admission.session_id
        or any(context.to_dict().get(key) != value for key, value in request.scope)
    ):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "state query is outside the admitted scope")
    validate_turn(request, admission, tool_name=STATE_RETRIEVAL_TOOL, scheduler=service.scheduler)
    if section == "selection":
        value = [
            entry["selection"]
            for entry in state.get("ingress", ())
            if entry["message_id"] in request.source_message_ids
        ]
    else:
        value = [asdict(record) for record in getattr(request, section)]
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(f"{request.request_id}:{request.revision}:{section}:{text}".encode()).hexdigest()
    return request, text, digest


def read_request_state(service, context, admission, section, offset=0, limit=2000, expected_digest=""):
    """Return a character page; continuation pages require the same section version."""
    if (
        type(offset) is not int
        or type(limit) is not int
        or offset < 0
        or not 1 <= limit <= 8000
        or not isinstance(expected_digest, str)
        or (offset > 0 and not expected_digest)
    ):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid state query range or missing continuation digest")
    request, text, digest = state_section(service, context, admission, section)
    if expected_digest and expected_digest != digest:
        raise RequestError(
            "REQUEST_STATE_CHANGED", "state section changed; explicitly restart the query at offset zero"
        )
    if offset > len(text):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "state offset exceeds the section length")
    end = min(offset + limit, len(text))
    return {
        "request_id": request.request_id,
        "revision": request.revision,
        "section": section,
        "digest": digest,
        "offset": offset,
        "limit": limit,
        "text": text[offset:end],
        "total_chars": len(text),
        "next_offset": end if end < len(text) else None,
        "material_only": True,
    }
