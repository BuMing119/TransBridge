# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze review inputs on Qt and prepare result maps in the existing worker."""

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.result_merge import ResultEntryState
from transbridge.ui.project_rows import ProjectRowsCollection

from .task_session_merge import entry_state


def saved_state(value):
    return ResultEntryState(
        EntryKey.from_dict(value["entry_key"]),
        value["original"],
        value["translation"],
        value["stage"],
        value.get("context", ""),
        value.get("deleted", False),
        value.get("source_locator"),
    )


def review_stamp(ctx):
    """GUI-only identity/revision check, including replaced source collections."""
    return (
        ctx.active_version_identity,
        getattr(ctx, "project_revision", None),
        getattr(ctx, "variant_revision", None),
        tuple((key, id(slot.collection), slot.collection.collection_revision) for key, slot in ctx.slots.items()),
    )


def freeze_current_reads(ctx):
    reads = []
    for slot in ctx.slots.values():
        collection = slot.collection
        if isinstance(collection, ProjectRowsCollection):
            read = collection.freeze_entries()
            reads.append(lambda keys, read=read: (entry_state(entry) for entry in read(keys)))
        else:
            # Legacy/resident collections are already in memory. Freeze their mutable
            # entries here; no live collection or Qt object crosses into the worker.
            states = {entry.identity: entry_state(entry) for entry in collection}
            reads.append(lambda keys, states=states: (states[key] for key in keys if key in states))
    return tuple(reads)


def prepare_review(record, payload, evidence, reads, identity):
    rows = {EntryKey.from_dict(row["entry_key"]): row for row in (payload or {}).get("rows", ())}
    previous = {}
    for item in evidence:
        applied = {EntryKey.from_dict(key) for key in item.get("applied_keys", ())}
        for conflict in item["conflicts"]:
            key = EntryKey.from_dict(conflict["entry_key"])
            if key in applied and conflict["current"] is not None and conflict["decision"] == "prefer_incoming":
                previous.setdefault(key, []).insert(0, saved_state(conflict["current"]))
    current = {}
    if identity == (record["project_id"], record["variant_id"]):
        for read in reads:
            missing = tuple(key for key in rows if key not in current)
            if not missing:
                break
            current.update((state.entry_key, state) for state in read(missing))
    return record, rows, previous, current
