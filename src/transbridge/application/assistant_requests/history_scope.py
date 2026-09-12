"""Derive message ownership from authoritative request and result references."""


def assign_history_requests(history, requests, message_owners):
    """Return an ownership projection; preserve shared inputs and original evidence."""
    owners = {identity: {owner} for identity, owner in message_owners.items()}
    for request in requests:
        sources = (*request.source_message_ids, *(revision.source_message_id for revision in request.revisions))
        for identity in (*sources, *(evidence.reference for evidence in request.evidence if evidence.kind == "answer")):
            owners.setdefault(identity, set()).add(request.request_id)
    records = []
    calls = {}
    for source in history:
        record = dict(source)
        identity = record.get("message_id")
        if identity in owners:
            record["request_ids"] = sorted(owners[identity])
        tool_calls = record.get("tool_calls", ())
        if any(call.get("name") == "submit_request_routing" for call in tool_calls):
            record["request_ids"] = []
        for call in tool_calls:
            if "request_ids" in record:
                calls[call["id"]] = record["request_ids"]
        if record.get("role") == "tool" and record.get("tool_call_id") in calls:
            record.setdefault("request_ids", calls[record["tool_call_id"]])
        records.append(record)
    return records
