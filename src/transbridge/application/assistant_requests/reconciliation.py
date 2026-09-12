"""Explicit outcome reconciliation; a resolution never dispatches or replays work."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import OwnerRef, TaskAccessError

from .admission import get_effect, record_effect_outcome
from .dispatch import reconcile_dispatches, record_dispatch_outcome
from .journal import EventCause
from .models import EffectStatus, Evidence, RequestError, UserRequest, digest
from .reducer import add_evidence, converge


@dataclass(frozen=True, slots=True)
class EffectResolution:
    command_id: str
    effect_id: str
    expected_revision: int
    operation_hash: str
    status: str
    reference: str
    authority: str
    source_message_id: str = ""
    complete: bool = False


def reconcile_effect(request: UserRequest, resolution: EffectResolution) -> UserRequest:
    """The caller must validate user ingress or adapter receipts and persist atomically."""
    fingerprint = digest(asdict(resolution))
    prior = dict(request.applied_events).get(resolution.command_id)
    if prior:
        if prior != fingerprint:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "resolution command ID was reused")
        return request
    if resolution.expected_revision != request.revision:
        raise RequestError("REQUEST_REVISION_CONFLICT", "reload the request before recording reconciliation")
    if not resolution.command_id or not resolution.reference.strip():
        raise RequestError("REQUEST_PROTOCOL_INVALID", "resolution requires a command ID and a concrete reference")
    if resolution.authority not in {"verified_receipt", "user_attestation"}:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "model text and runtime absence are not outcome proof")
    if resolution.authority == "user_attestation" and not resolution.source_message_id:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "manual reconciliation requires an accepted user action")
    effect = get_effect(request, resolution.effect_id)
    if resolution.operation_hash != effect.operation_hash:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "resolution belongs to another logical operation")
    if effect.status != EffectStatus.OUTCOME_UNKNOWN:
        raise RequestError("REQUEST_RECONCILIATION_INVALID", "only an unresolved outcome can be explicitly reconciled")
    status = EffectStatus(resolution.status)
    if status not in {EffectStatus.SUCCEEDED, EffectStatus.FAILED, EffectStatus.CANCELLED}:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "resolution must declare a known outcome")
    audit = {
        **asdict(resolution),
        "previous_status": effect.status.value,
        "previous_receipt": effect.receipt,
        "attempt_id": effect.execution.attempt_id,
        "effect_revision": effect.execution.request_revision,
    }
    evidence = Evidence(
        f"reconciliation:{resolution.command_id}",
        "reconciliation",
        effect.execution.request_revision,
        effect.execution.item_ids,
        json.dumps(audit, ensure_ascii=False, sort_keys=True),
        complete=False,
    )
    updated = add_evidence(request, evidence, satisfy_items=False)
    updated = record_effect_outcome(
        updated,
        effect.effect_id,
        status=status,
        receipt=json.dumps(
            {
                "authority": resolution.authority,
                "reference": resolution.reference,
                "source_message_id": resolution.source_message_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        sequence=effect.last_sequence + 1,
        job_id=effect.job_id,
        run_id=effect.run_id,
        item_complete=resolution.complete,
        satisfy_item=resolution.complete
        and not any(d.dispatch_id == effect.execution.dispatch_id for d in request.dispatches),
    )
    # Keep known results but require explicit continuation; reconciliation itself never resumes work.
    ids = set(effect.execution.item_ids)
    if updated.revision == effect.execution.request_revision and not updated.terminal:
        items = tuple(
            replace(
                item,
                waiting_reasons=tuple(
                    dict.fromkeys((
                        *(reason for reason in item.waiting_reasons if reason != "outcome_unknown"),
                        "reconciled_result",
                    ))
                ),
            )
            if item.item_id in ids
            else item
            for item in updated.items
        )
        updated = replace(updated, items=items)
    updated = reconcile_dispatches(updated)
    return replace(converge(updated), applied_events=(*updated.applied_events, (resolution.command_id, fingerprint)))


def reconcile_dispatch(
    request: UserRequest,
    dispatch_id: str,
    *,
    command_id: str,
    expected_revision: int,
    status: str,
    reference: str,
    authority: str,
    source_message_id: str = "",
) -> UserRequest:
    """Record a proved parent outcome separately after every leaf outcome is known."""
    payload = {
        "dispatch_id": dispatch_id,
        "expected_revision": expected_revision,
        "status": status,
        "reference": reference,
        "authority": authority,
        "source_message_id": source_message_id,
    }
    fingerprint = digest(payload)
    previous = dict(request.applied_events).get(command_id)
    if previous:
        if previous != fingerprint:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "resolution command ID was reused")
        return request
    if expected_revision != request.revision:
        raise RequestError("REQUEST_REVISION_CONFLICT", "parent resolution revision changed")
    if (
        not command_id
        or not reference
        or authority not in {"verified_receipt", "user_attestation"}
        or (authority == "user_attestation" and not source_message_id)
    ):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "parent resolution requires explicit verified provenance")
    dispatch = next((d for d in request.dispatches if d.dispatch_id == dispatch_id), None)
    if dispatch is None or dispatch.status != "outcome_unknown":
        raise RequestError("REQUEST_RECONCILIATION_INVALID", "only an unknown parent dispatch may be reconciled")
    if status not in {"completed", "failed", "cancelled"}:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid parent outcome")
    if any(
        e.status in {EffectStatus.PREPARED, EffectStatus.BOUND, EffectStatus.RUNNING, EffectStatus.OUTCOME_UNKNOWN}
        for e in request.effects
        if e.execution.dispatch_id == dispatch_id
    ):
        raise RequestError("OUTCOME_UNKNOWN", "resolve every leaf outcome before the parent")
    evidence = Evidence(
        f"reconciliation:{command_id}",
        "reconciliation",
        dispatch.execution.request_revision,
        dispatch.execution.item_ids,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        complete=False,
    )
    updated = add_evidence(request, evidence, satisfy_items=False)
    updated = record_dispatch_outcome(updated, dispatch_id, status=status, sequence=dispatch.last_sequence + 1)
    return replace(updated, applied_events=(*updated.applied_events, (command_id, fingerprint)))


class RequestReconciliationCoordinator:
    """Small callable application entrypoint with live-worker and accepted-source checks."""

    def __init__(self, service, runtime=None):
        self.service = service
        self.runtime = runtime

    def resolve_user(
        self, context, request_id, record_id, *, is_dispatch, expected_revision, status, reference, command_id
    ):
        """Persist the explicit user observation and its outcome in one transaction."""
        text = f"核对请求结果：{status}。依据：{reference}"

        def apply(state):
            requests = list(self.service.requests(state))
            index = next(i for i, request in enumerate(requests) if request.request_id == request_id)
            request = requests[index]
            record = (
                next(d for d in request.dispatches if d.dispatch_id == record_id)
                if is_dispatch
                else get_effect(request, record_id)
            )
            if record.job_id:
                if self.runtime is None:
                    raise RequestError("REQUEST_RECONCILIATION_INVALID", "无法核对原任务是否已停止。")
                scope = dict(request.scope)
                actor = OwnerRef(
                    context.owner_id,
                    "smart-assistant",
                    scope.get("project_id"),
                    scope.get("variant_id"),
                    context.session_id,
                    context.permissions,
                )
                try:
                    live = self.runtime.get(JobRef(record.job_id, context.owner_id, record.run_id), actor)
                except TaskAccessError as error:
                    if error.code != "job_not_found":
                        raise
                else:
                    if not live.is_terminal:
                        raise RequestError("REQUEST_RECONCILIATION_INVALID", "原任务仍能提交结果，请先等待它停止。")
            if is_dispatch:
                updated = reconcile_dispatch(
                    request,
                    record_id,
                    command_id=command_id,
                    expected_revision=expected_revision,
                    status=status,
                    reference=reference,
                    authority="user_attestation",
                    source_message_id=command_id,
                )
            else:
                updated = reconcile_effect(
                    request,
                    EffectResolution(
                        command_id,
                        record_id,
                        expected_revision,
                        record.operation_hash,
                        status,
                        reference,
                        "user_attestation",
                        command_id,
                        status == "succeeded",
                    ),
                )
            requests[index] = updated
            state["requests"] = [r.to_dict() for r in requests]
            ingress = state.setdefault("ingress", [])
            if not any(i["message_id"] == command_id for i in ingress):
                ingress.append({
                    "message_id": command_id,
                    "text": text,
                    "sequence": len(ingress) + 1,
                    "context": context.to_dict(),
                    "selection": {},
                    "status": "applied",
                    "batch_id": "",
                    "digest": digest({"text": text, "request_id": request_id, "record_id": record_id}),
                })

        self.service.transact(
            context,
            apply,
            append_messages=({"message_id": command_id, "role": "user", "content": text},),
            cause=EventCause(
                "request.reconciled",
                "user",
                {
                    "request_ids": [request_id],
                    "message_ids": [command_id],
                    "dispatch_ids" if is_dispatch else "effect_ids": [record_id],
                },
            ),
        )
        resources = getattr(self.service, "request_resources", None)
        if resources is not None and not is_dispatch:
            resources.release(record_id)
        self.service.notify(context.session_id)

    def resolve_effect(self, context, request_id: str, resolution: EffectResolution) -> UserRequest:
        with self.service.serialized(context):
            state = self.service.state(context)
            if resolution.authority == "user_attestation":
                source = next(
                    (i for i in state.get("ingress", ()) if i["message_id"] == resolution.source_message_id), None
                )
                if source is None or not source.get("text", "").strip():
                    raise RequestError("REQUEST_PROTOCOL_INVALID", "manual resolution must cite persisted user input")

            def update(request):
                effect = get_effect(request, resolution.effect_id)
                if effect.job_id and self.runtime is None:
                    raise RequestError(
                        "REQUEST_RECONCILIATION_INVALID", "a live-runtime check is required for bound jobs"
                    )
                if effect.job_id and self.runtime is not None:
                    actor = OwnerRef(
                        context.owner_id,
                        "smart-assistant",
                        context.project_id,
                        context.variant_id,
                        context.session_id,
                        context.permissions,
                    )
                    try:
                        snapshot = self.runtime.get(JobRef(effect.job_id, context.owner_id, effect.run_id), actor)
                    except TaskAccessError as error:
                        if error.code != "job_not_found":
                            raise
                    else:
                        if not snapshot.is_terminal:
                            raise RequestError("REQUEST_RECONCILIATION_INVALID", "the worker is still able to submit")
                return reconcile_effect(request, resolution)

            updated = self.service.update_request(context, request_id, update)
        resources = getattr(self.service, "request_resources", None)
        if resources is not None:
            resources.release(resolution.effect_id)
        self.service.notify(context.session_id)
        return updated
