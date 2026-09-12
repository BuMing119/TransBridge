"""Stage immutable transcript evidence and merge Session history projections."""

from __future__ import annotations

from dataclasses import replace

from .models import RequestError, digest
from .transcript import TranscriptManifest, TranscriptMessage


def _record(message: TranscriptMessage) -> dict:
    data = message.to_dict()
    record = {"message_id": data["message_id"], "role": data["role"], "content": data["content"], **data["metadata"]}
    if data["tool_calls"]:
        record["tool_calls"] = data["tool_calls"]
    if data["tool_call_id"] is not None:
        record["tool_call_id"] = data["tool_call_id"]
    return record


def _reuse_frozen(records, previous, frozen):
    """Reuse already validated immutable messages instead of freezing them again."""
    by_id = {
        message.get("message_id"): (message, value)
        for message, value in zip(previous, frozen)
        if message.get("message_id")
    }
    return tuple(
        by_id[record["message_id"]][1]
        if record.get("message_id") in by_id and by_id[record["message_id"]][0] == record
        else record
        for record in records
    )


def stage_transcript(snapshot, records, store, *, visible_messages=None):
    """Stage attachments and retain other writers' admitted messages on CAS retry.

    The enclosing lifecycle transaction publishes the returned manifest once.
    Current system instructions are a projection; older system prompt versions
    stay in the immutable ledger but are not reintroduced into the model input.
    """
    if store is None:
        changes = {"backend_history": tuple(records)}
        if visible_messages is not None:
            changes["messages"] = tuple(visible_messages)
        return replace(snapshot, **changes)
    normalized = [
        {**record, "message_id": record.get("message_id") or f"legacy-{index}-{digest(record)}"}
        for index, record in enumerate(records)
    ]
    manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
    existing = store.read(snapshot.ref.identity.value, manifest)
    known = {message.message_id: message for message in existing}
    additions = []
    for message in normalized:
        mid = message["message_id"]
        old = known.get(mid)
        if old is not None:
            old_record = _record(old)
            if any(
                old_record.get(key) != message.get(key, [] if key == "tool_calls" else None)
                for key in ("role", "content", "tool_call_id", "tool_calls")
            ):
                raise RequestError("COMMAND_PAYLOAD_CONFLICT", "immutable message content changed")
            continue
        record = TranscriptMessage(
            mid,
            len(existing) + len(additions) + 1,
            message["role"],
            message.get("content", ""),
            tool_call_id=message.get("tool_call_id"),
            tool_calls=tuple(message.get("tool_calls", ())),
            origin="user" if message["role"] == "user" else "runtime",
            metadata={
                key: value
                for key, value in message.items()
                if key not in {"message_id", "role", "content", "tool_calls", "tool_call_id"}
            },
        )
        known[mid] = record
        additions.append(record)
    manifest = store.append(snapshot.ref.identity.value, manifest, tuple(additions))
    combined = existing + tuple(additions)
    # Merge from the committed ledger, so stale whole-history arguments cannot
    # remove an input accepted by another writer between load and conditional save.
    merged = [_record(message) for message in combined if message.role != "system"]
    system = next((message for message in reversed(combined) if message.role == "system"), None)
    if system is not None:
        merged.insert(0, _record(system))
    previous = snapshot.backend_messages()
    changes = {
        "backend_history": _reuse_frozen(merged, previous, snapshot.backend_history),
        "transcript_manifest": manifest.to_dict(),
    }
    if visible_messages is not None:
        visible = merged if visible_messages == records else visible_messages
        changes["messages"] = _reuse_frozen(visible, snapshot.visible_messages(), snapshot.messages)
    return replace(snapshot, **changes)
