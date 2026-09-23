"""Decision material for the assistant, never a serialization of execution recovery state."""

from collections import Counter

from transbridge.application.assistant_requests.models import UNSETTLED_EFFECTS, ItemKind, ItemStatus


def decision_context(request, admission, state):
    """Keep decision-relevant facts exact; detailed execution records stay queryable.

    This is the assistant's next decision/answer, not an instruction to replay a
    checkpoint. The scheduler and execution gates remain the authority to act.
    """
    active = [i for i in request.items if i.status not in {ItemStatus.SATISFIED, ItemStatus.CANCELLED}]
    ready = [i for i in active if i.item_id in admission.ready_item_ids]
    kinds = {i.kind for i in ready}
    purpose = (
        "answer"
        if kinds == {ItemKind.ANSWER}
        else "execute"
        if kinds == {ItemKind.EXECUTION}
        else "execute_and_answer"
        if kinds
        else "reconcile"
    )
    dependencies = {identity for item in active for identity in item.dependencies}
    settled = Counter(i.status.value for i in request.items if i.status in {ItemStatus.SATISFIED, ItemStatus.CANCELLED})
    return {
        "purpose": purpose,
        "request_id": request.request_id,
        "goal": request.goal,
        "revision": request.revision,
        "round_restoration": [
            {"round_id": identity, "status": record["status"], "limitations": record.get("limitations", [])}
            for identity, record in sorted(
                state.get("undo_rounds", {}).items(),
                key=lambda item: item[1].get("undo_sequence", 0),
            )
            if record["status"] != "recording"
            and any(effect["request_id"] == request.request_id for effect in record["effects"].values())
        ][-1:],
        "constraints": list(request.constraints),
        "active_items": [
            {key: value for key, value in item.to_dict().items() if key != "evidence_ids"} for item in active
        ],
        "ready_item_ids": [i.item_id for i in ready],
        "dependency_status": {i.item_id: i.status.value for i in request.items if i.item_id in dependencies},
        "settled_counts": dict(sorted(settled.items())),
        "unsettled_operations": [
            {
                "effect_id": effect.effect_id,
                "status": effect.status.value,
                "item_ids": list(effect.execution.item_ids),
                "request_revision": effect.execution.request_revision,
                "job_id": effect.job_id,
                "run_id": effect.run_id,
            }
            for effect in request.effects
            if effect.status in UNSETTLED_EFFECTS
        ],
        "unsettled_dispatches": [
            {
                "dispatch_id": dispatch.dispatch_id,
                "status": dispatch.status,
                "item_ids": list(dispatch.execution.item_ids),
                "request_revision": dispatch.execution.request_revision,
                "job_id": dispatch.job_id,
                "run_id": dispatch.run_id,
            }
            for dispatch in request.dispatches
            if dispatch.status in {"running", "outcome_unknown"}
        ],
        "details": {
            "tool": "read_request_state",
            "sections": ["items", "effects", "evidence", "dispatches", "selection"],
            "instruction": "Use recent tool results first; query details only when needed. Never replay settled work.",
        },
        **(
            {"answer_protocol": "Complete text plus report_answer_coverage for answer items; never claim execution."}
            if ItemKind.ANSWER in kinds
            else {}
        ),
        "selection": [
            i["selection"] for i in state.get("ingress", ()) if i["message_id"] in request.source_message_ids
        ],
    }
