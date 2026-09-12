"""Conservative restart reconciliation; unknown effects are never replayed."""

from dataclasses import replace

from .admission import record_effect_outcome
from .models import EffectStatus, ItemStatus, UserRequest
from .reducer import converge


def recover_request(
    request: UserRequest,
    *,
    live_job_ids: frozenset[str] = frozenset(),
    proven_outcomes: dict[str, tuple[str, str]] | None = None,
) -> UserRequest:
    """Only adapter-verified receipts may settle effects; missing jobs are not proof."""
    updated = request
    for effect in request.effects:
        if effect.status not in (
            EffectStatus.PREPARED,
            EffectStatus.BOUND,
            EffectStatus.RUNNING,
            EffectStatus.OUTCOME_UNKNOWN,
        ):
            continue
        proof = (proven_outcomes or {}).get(effect.effect_id)
        if proof:
            updated = record_effect_outcome(
                updated,
                effect.effect_id,
                status=proof[0],
                receipt=proof[1],
                sequence=effect.last_sequence + 1,
                job_id=effect.job_id,
                run_id=effect.run_id,
            )
        elif not effect.job_id or effect.job_id not in live_job_ids:
            updated = record_effect_outcome(
                updated,
                effect.effect_id,
                status=EffectStatus.OUTCOME_UNKNOWN,
                receipt=effect.receipt,
                sequence=effect.last_sequence + 1,
                job_id=effect.job_id,
                run_id=effect.run_id,
            )
    # UI approval tokens are never persisted authority. The item still needs approval.
    items = tuple(
        replace(
            i,
            status=ItemStatus.WAITING,
            waiting_reasons=tuple("approval_revalidation" if r == "confirmation" else r for r in i.waiting_reasons),
        )
        if "confirmation" in i.waiting_reasons
        else i
        for i in updated.items
    )
    dispatches = tuple(
        replace(d, status="outcome_unknown") if d.status == "running" and d.job_id not in live_job_ids else d
        for d in updated.dispatches
    )
    return converge(replace(updated, items=items, dispatches=dispatches, lease_epoch=updated.lease_epoch + 1))


def request_recovery_preflight(service):
    """Preserve legacy recovery, reject association-blind retries of tracked effects."""
    from transbridge.application.contracts import RequestContext

    from .models import RequestError

    def check(run_id, owner, actor):
        if not getattr(owner, "session_id", None):
            return
        context = RequestContext(
            owner_id=owner.owner_id,
            session_id=owner.session_id,
            project_id=owner.project_id,
            variant_id=owner.variant_id,
            permissions=actor.permissions,
        )
        requests = service.requests(service.state(context))
        if any(record.run_id == run_id for request in requests for record in (*request.effects, *request.dispatches)):
            raise RequestError(
                "REQUEST_RECOVERY_REQUIRED", "该任务属于助手请求，请在原请求中核对结果并明确继续；不能直接重放旧任务。"
            )

    return check
