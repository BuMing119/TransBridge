"""Only authoritative request state can supply goals, constraints and evidence."""


def required_state(request, admission, state):
    details = request.to_dict()
    return {
        "request_id": request.request_id,
        "goal": request.goal,
        "revision": request.revision,
        "constraints": list(request.constraints),
        "items": [item.to_dict() for item in request.items],
        "ready_item_ids": list(admission.ready_item_ids),
        "answer_protocol": "Complete text plus report_answer_coverage for answer items; never claim execution.",
        **{key: details.get(key, []) for key in ("effects", "evidence", "dispatches")},
        "selection": [
            i["selection"] for i in state.get("ingress", ()) if i["message_id"] in request.source_message_ids
        ],
    }
