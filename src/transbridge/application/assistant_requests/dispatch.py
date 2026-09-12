"""Parent plan completion is separate from leaf effect identities and receipts."""

from dataclasses import replace

from .models import EffectStatus, Evidence, ItemStatus, RequestError
from .reducer import add_evidence, converge


def record_dispatch_outcome(request, dispatch_id: str, *, status: str, sequence: int):
    dispatch = next((d for d in request.dispatches if d.dispatch_id == dispatch_id), None)
    if dispatch is None:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "plan dispatch identity is unknown")
    if sequence <= dispatch.last_sequence:
        return request
    updated = replace(
        request,
        dispatches=tuple(
            replace(d, status=status, last_sequence=sequence) if d.dispatch_id == dispatch_id else d
            for d in request.dispatches
        ),
    )
    return reconcile_dispatches(updated)


def reconcile_dispatches(request):
    updated = request
    for dispatch in request.dispatches:
        if dispatch.status in ("running", "outcome_unknown"):
            continue
        effects = [e for e in updated.effects if e.execution.dispatch_id == dispatch.dispatch_id]
        if not effects:
            continue  # A batch of auxiliary reads never proves an execution goal complete.
        if any(e.status in ("prepared", "bound", "running", "outcome_unknown") for e in effects):
            continue
        if (
            dispatch.status == "completed"
            and effects
            and all(e.status == EffectStatus.SUCCEEDED and e.result_complete for e in effects)
        ):
            updated = add_evidence(
                updated,
                Evidence(
                    f"dispatch:{dispatch.dispatch_id}",
                    "execution",
                    dispatch.execution.request_revision,
                    dispatch.execution.item_ids,
                    f"job:{dispatch.job_id}/{dispatch.run_id}",
                ),
            )
        elif not updated.terminal and dispatch.execution.request_revision == updated.revision:
            ids = set(dispatch.execution.item_ids)
            updated = replace(
                updated,
                items=tuple(
                    replace(i, status=ItemStatus.FAILED, waiting_reasons=("plan_failed",)) if i.item_id in ids else i
                    for i in updated.items
                ),
            )
    return converge(updated)
