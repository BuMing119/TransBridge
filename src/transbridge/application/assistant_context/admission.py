"""Publish staged context references under the existing Session CAS and turn lease."""

from dataclasses import replace

from transbridge.application.assistant_requests.models import RequestError, digest
from transbridge.application.assistant_requests.scheduler import ready_item_ids
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.persistence.v2.ids import SessionId, SessionRef

from .models import PreparationWait


def require_admitted(service, admission, state):
    service.scheduler.validate(admission)
    request = next((r for r in service.requests(state) if r.request_id == admission.request_id), None)
    if (
        state.get("session_tombstone")
        or request is None
        or request.terminal
        or request.pause_reasons
        or request.session_id != admission.session_id
        or request.scope != admission.scope
        or request.revision != admission.request_revision
        or request.lease_epoch != admission.request_lease_epoch
        or not set(admission.ready_item_ids) <= set(ready_item_ids(request))
    ):
        raise RequestError("TURN_LEASE_STALE", "request changed during context preparation")
    return request


def publish_context(
    service,
    context,
    admission,
    staged,
    expected_head,
    *,
    still_current=lambda: True,
    validate_sources=lambda snapshot, state: None,
):
    """The candidate is already on disk; retries perform no network calls."""
    errors = []

    def publish(snapshot):
        try:
            if not still_current():
                raise PreparationWait("CONTEXT_PREPARATION_INTERRUPTED", "上下文配置或准备资格已变化。")
            state = snapshot.assistant_data()
            require_admitted(service, admission, state)
            current = state.get("context_heads", {}).get(admission.request_id)
            if current != expected_head:
                raise PreparationWait("CONTEXT_HEAD_CONFLICT", "另一轮已更新上下文，请重试。")
            validate_sources(snapshot, state)
            state.setdefault("context_heads", {})[admission.request_id] = staged.head
            state.setdefault("context_waits", {}).pop(admission.request_id, None)
            state.setdefault("context_pending", {}).pop(admission.request_id, None)
            manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
            refs = {ref.path: ref for ref in manifest.artifacts}
            refs.update({ref.path: ref for ref in staged.references})
            manifest = replace(manifest, artifacts=tuple(refs.values()))
            return replace(snapshot, assistant_state=state, transcript_manifest=manifest.to_dict())
        except Exception as exc:
            errors.append(exc)
            raise

    with service.serialized(context):
        result = service.lifecycle.transact(SessionRef(SessionId(context.session_id)), context, publish, publish=False)
    if not result.is_success:
        if errors:
            raise errors[-1]
        raise PreparationWait("CONTEXT_PERSIST_FAILED", "; ".join(d.message for d in result.diagnostics))
    return staged


def save_wait(service, context, admission, error, *, config_digest, pending=None):
    def update(snapshot):
        state = snapshot.assistant_data()
        request = require_admitted(service, admission, state)
        state.setdefault("context_waits", {})[admission.request_id] = {
            "code": error.code,
            "message": str(error),
            "request_revision": admission.request_revision,
            "config_digest": config_digest,
            "material_digest": material_digest(state, request),
        }
        manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
        if pending is not None:
            state.setdefault("context_pending", {})[admission.request_id] = {
                "head": pending.head,
                "request_revision": admission.request_revision,
                "expected_head": state.get("context_heads", {}).get(admission.request_id),
                "ownership_digest": digest({**state.get("result_owners", {}), **state.get("message_owners", {})}),
            }
            refs = {r.path: r for r in (*manifest.artifacts, *pending.references)}
            manifest = replace(manifest, artifacts=tuple(refs.values()))
        return replace(snapshot, assistant_state=state, transcript_manifest=manifest.to_dict())

    with service.serialized(context):
        result = service.lifecycle.transact(SessionRef(SessionId(context.session_id)), context, update, publish=False)
    if not result.is_success:
        raise PreparationWait("CONTEXT_WAIT_PERSIST_FAILED", "无法保存上下文等待状态。")


def clear_wait(service, context, request_id):
    """Explicit retry clears only context waiting; business pauses remain intact."""
    service.transact(context, lambda state: state.setdefault("context_waits", {}).pop(request_id, None))


def available_requests(state, requests, config_digest):
    waits = state.get("context_waits", {})
    return tuple(
        r
        for r in requests
        if not (
            r.request_id in waits
            and waits[r.request_id].get("request_revision") == r.revision
            and waits[r.request_id].get("config_digest") == config_digest
            and waits[r.request_id].get("material_digest") == material_digest(state, r)
        )
    )


def material_digest(state, request):
    """Actual new input/results can unblock preparation; lease churn cannot bill another summary."""
    values = request.to_dict()
    return digest({
        "request": {
            key: values.get(key)
            for key in (
                "goal",
                "revision",
                "constraints",
                "items",
                "evidence",
                "effects",
                "dispatches",
                "source_message_ids",
            )
        },
        "recorded_ids": state.get("recorded_ids", []),
        "owners": {**state.get("result_owners", {}), **state.get("message_owners", {})},
    })
