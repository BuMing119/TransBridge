"""Effect preparation and result reconciliation; persistence remains the caller's boundary."""

from dataclasses import replace

from .models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    Evidence,
    ItemKind,
    ItemStatus,
    RequestError,
    UserRequest,
    digest,
)
from .reducer import add_evidence, converge, require_open
from .scheduler import RequestScheduler, TurnAdmission


def validate_turn(
    request: UserRequest,
    admission: TurnAdmission,
    *,
    tool_name: str,
    scheduler: RequestScheduler,
    item_ids: tuple[str, ...] = (),
) -> None:
    scheduler.validate(admission)
    require_open(request, admission.request_revision)
    if request.pause_reasons:
        raise RequestError("REQUEST_PAUSED", "resume the request before admitting new operations")
    if (
        admission.stage != "execution"
        or admission.request_id != request.request_id
        or admission.session_id != request.session_id
        or admission.scope != request.scope
        or admission.request_lease_epoch != request.lease_epoch
    ):
        raise RequestError("TURN_LEASE_STALE", "execution identity is not valid for this turn")
    if tool_name not in admission.allowed_tools or not set(item_ids) <= set(admission.ready_item_ids):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "tool or item is outside the admitted execution set")


def _replace_effect(request: UserRequest, effect: EffectIntent) -> UserRequest:
    return replace(request, effects=tuple(effect if e.effect_id == effect.effect_id else e for e in request.effects))


def get_effect(request: UserRequest, effect_id: str) -> EffectIntent:
    for effect in request.effects:
        if effect.effect_id == effect_id:
            return effect
    raise RequestError("REQUEST_PROTOCOL_INVALID", "effect intent does not exist")


def prepare_effect(
    request: UserRequest,
    admission: TurnAdmission,
    execution: AssistantExecutionRef,
    *,
    effect_id: str,
    operation: dict,
    tool_name: str,
    scheduler: RequestScheduler,
    approval_id: str = "",
) -> UserRequest:
    validate_turn(request, admission, tool_name=tool_name, scheduler=scheduler, item_ids=execution.item_ids)
    if (
        execution.request_id != request.request_id
        or execution.request_revision != request.revision
        or execution.session_id != request.session_id
        or execution.turn_id != admission.turn_id
        or not execution.item_ids
        or not execution.attempt_id
        or not execution.dispatch_id
        or not effect_id
    ):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "effect requires program-issued execution identity")
    kinds = {i.item_id: i.kind for i in request.items}
    if any(i.waiting_reasons for i in request.items if i.item_id in execution.item_ids):
        raise RequestError("REQUEST_PAUSED", "execution item still has an independent waiting reason")
    if any(kinds.get(i) != ItemKind.EXECUTION for i in execution.item_ids):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "write effects may only belong to execution items")
    fingerprint = digest(operation)
    existing = next((e for e in request.effects if e.effect_id == effect_id), None)
    if existing:
        if existing.operation_hash != fingerprint or existing.execution != execution:
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "effect ID already belongs to a different operation")
        return request
    effect = EffectIntent(effect_id, fingerprint, execution, request.lease_epoch, approval_id=approval_id)
    return replace(request, effects=(*request.effects, effect))


def validate_effect(request: UserRequest, effect_id: str) -> EffectIntent:
    effect = get_effect(request, effect_id)
    require_open(request, effect.execution.request_revision)
    if effect.lease_epoch != request.lease_epoch:
        raise RequestError("REQUEST_PAUSED", "effect permission was revoked or request paused")
    if any(i.waiting_reasons for i in request.items if i.item_id in effect.execution.item_ids):
        raise RequestError("REQUEST_PAUSED", "execution item is blocked by a user control or unresolved result")
    if effect.status not in (EffectStatus.PREPARED, EffectStatus.BOUND, EffectStatus.RUNNING):
        raise RequestError("OUTCOME_UNKNOWN", "settled or unknown operation cannot be dispatched again")
    return effect


def bind_job(request: UserRequest, effect_id: str, *, job_id: str, run_id: str) -> UserRequest:
    effect = validate_effect(request, effect_id)
    if not job_id or not run_id or (effect.job_id and (effect.job_id, effect.run_id) != (job_id, run_id)):
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "job identity missing or already bound differently")
    return _replace_effect(request, replace(effect, job_id=job_id, run_id=run_id, status=EffectStatus.BOUND))


def activate_effect(request: UserRequest, effect_id: str) -> UserRequest:
    effect = validate_effect(request, effect_id)
    return _replace_effect(request, replace(effect, status=EffectStatus.RUNNING))


def record_effect_outcome(
    request: UserRequest,
    effect_id: str,
    *,
    status: EffectStatus | str,
    receipt: str = "",
    sequence: int = 0,
    job_id: str = "",
    run_id: str = "",
    item_complete: bool = True,
    satisfy_item: bool = True,
) -> UserRequest:
    effect = get_effect(request, effect_id)
    status = EffectStatus(status)
    if effect.job_id and (job_id, run_id) != (effect.job_id, effect.run_id):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "result job/run does not match the admitted operation")
    if sequence <= effect.last_sequence:
        if sequence == effect.last_sequence and (status, receipt) != (effect.status, effect.receipt):
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "result sequence reused with conflicting outcome")
        return request
    if status not in (
        EffectStatus.SUCCEEDED,
        EffectStatus.FAILED,
        EffectStatus.CANCELLED,
        EffectStatus.OUTCOME_UNKNOWN,
    ):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "result must be a terminal or unknown outcome")
    if effect.status in (EffectStatus.SUCCEEDED, EffectStatus.FAILED, EffectStatus.CANCELLED):
        return request
    if status == EffectStatus.SUCCEEDED and not receipt:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "successful effects require an actual commit/outcome receipt")
    updated = _replace_effect(
        request, replace(effect, status=status, receipt=receipt, last_sequence=sequence, result_complete=item_complete)
    )
    if status == EffectStatus.SUCCEEDED:
        updated = add_evidence(
            updated,
            Evidence(
                f"effect:{effect_id}",
                "execution",
                effect.execution.request_revision,
                effect.execution.item_ids,
                receipt,
                complete=item_complete,
            ),
            satisfy_items=satisfy_item,
        )
    elif not request.terminal and request.revision == effect.execution.request_revision:
        ids = set(effect.execution.item_ids)
        reason = "outcome_unknown" if status == EffectStatus.OUTCOME_UNKNOWN else "execution_failed"
        item_status = ItemStatus.WAITING if status == EffectStatus.OUTCOME_UNKNOWN else ItemStatus.FAILED
        updated = replace(
            updated,
            items=tuple(
                replace(i, status=item_status, waiting_reasons=tuple(dict.fromkeys((*i.waiting_reasons, reason))))
                if i.item_id in ids
                else i
                for i in updated.items
            ),
        )
    return converge(updated)
