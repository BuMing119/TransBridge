"""Conflict-checked inverse commands for an assistant's Variant-owned edits.

Receipts contain only touched entries and label definitions. Applying an inverse
updates the active working copy through the lifecycle CAS; it does not save that
copy to disk or persist the receipt. Those are the caller's responsibilities.
"""

from dataclasses import dataclass, replace

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.variant import SourceFingerprint, VariantChangeSet, VariantEntryState, VariantSnapshot


def _unsupported(message):
    raise DomainError(ErrorCategory.PREREQUISITE, "ASSISTANT_UNDO_UNSUPPORTED", message)


def _conflict(message):
    raise DomainError(ErrorCategory.CONFLICT, "ASSISTANT_UNDO_CONFLICT", message)


def _mutable(entry):
    return entry.translation, entry.stage, entry.labels, entry.external_refs


def _check_pair(before, after):
    if before.ref != after.ref or before.source_fingerprints != after.source_fingerprints:
        _unsupported("Undo cannot cross Project/Variant identity or source fingerprint changes.")
    old = {entry.entry_key: entry for entry in before.entries}
    new = {entry.entry_key: entry for entry in after.entries}
    if old.keys() != new.keys():
        _unsupported("Entry creation and deletion require a separate inverse operation.")
    for key, entry in old.items():
        next_entry = new[key]
        if (entry.provenance, entry.tombstone, entry.inferred_fields) != (
            next_entry.provenance,
            next_entry.tombstone,
            next_entry.inferred_fields,
        ):
            _unsupported("Provenance, tombstone and inferred metadata changes are outside this undo contract.")
        if _mutable(entry) != _mutable(next_entry) and next_entry.revision.value <= entry.revision.value:
            _unsupported("Changed entries must have a newer committed revision.")
    if before != after and after.revision <= before.revision:
        _unsupported("Undo capture requires ordered committed Variant revisions.")


@dataclass(frozen=True, slots=True)
class VariantUndoReceipt:
    """Sparse before/after evidence; snapshots here are never restored wholesale."""

    before: VariantSnapshot
    after: VariantSnapshot

    def __post_init__(self):
        _check_pair(self.before, self.after)

    def to_dict(self):
        old = self.before.to_dto().envelope.data
        new = self.after.to_dto().envelope.data
        return {
            "schema_version": 1,
            "project_id": self.before.ref.project_id.value,
            "variant_id": self.before.ref.identity.value,
            "source_fingerprints": old["source_fingerprints"],
            "before_revision": self.before.revision,
            "after_revision": self.after.revision,
            "entries": [
                {"before": before.to_dict(), "after": after.to_dict()}
                for before, after in zip(self.before.entries, self.after.entries, strict=True)
            ],
            "label_library": {"before": old["label_library"], "after": new["label_library"]},
        }

    @classmethod
    def from_dict(cls, value):
        if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported assistant undo receipt version")
        ref = VariantRef(VariantId(value["variant_id"]), ProjectId(value["project_id"]))
        fingerprints = tuple(SourceFingerprint.from_dict(item) for item in value["source_fingerprints"])
        snapshots = []
        for side in ("before", "after"):
            snapshots.append(
                VariantSnapshot(
                    ref,
                    fingerprints,
                    tuple(VariantEntryState.from_dict(item[side]) for item in value["entries"]),
                    value[f"{side}_revision"],
                    tuple(value["label_library"][side].items()),
                )
            )
        return cls(*snapshots)


def capture_variant_undo(before: VariantSnapshot, after: VariantSnapshot) -> VariantUndoReceipt:
    """Capture only supported changed values from an attributed, committed command."""
    _check_pair(before, after)
    next_entries = {entry.entry_key: entry for entry in after.entries}
    keys = {entry.entry_key for entry in before.entries if _mutable(entry) != _mutable(next_entries[entry.entry_key])}
    old_library, new_library = dict(before.label_library), dict(after.label_library)
    missing = object()
    labels = {
        key
        for key in old_library.keys() | new_library.keys()
        if old_library.get(key, missing) != new_library.get(key, missing)
    }
    return VariantUndoReceipt(
        replace(
            before,
            entries=tuple(entry for entry in before.entries if entry.entry_key in keys),
            label_library=tuple((key, value) for key, value in before.label_library if key in labels),
        ),
        replace(
            after,
            entries=tuple(entry for entry in after.entries if entry.entry_key in keys),
            label_library=tuple((key, value) for key, value in after.label_library if key in labels),
        ),
    )


def build_variant_inverse(current: VariantSnapshot, receipt: VariantUndoReceipt, *, run_id: str) -> VariantChangeSet:
    """Validate every after-image before building one all-or-nothing inverse CAS."""
    if current.ref != receipt.after.ref or current.source_fingerprints != receipt.after.source_fingerprints:
        _conflict("The Project/Variant identity or source fingerprints changed after the assistant command.")
    if current.revision < receipt.after.revision:
        _conflict("The current Variant predates the recorded command.")
    if (receipt.before.label_library or receipt.after.label_library) and current.revision != receipt.after.revision:
        _conflict("Label definitions have no per-key revisions; a later Variant commit prevents safe library undo.")
    entries = {entry.entry_key: entry for entry in current.entries}
    before = {entry.entry_key: entry for entry in receipt.before.entries}
    replacements = {}
    for after in receipt.after.entries:
        if entries.get(after.entry_key) != after:
            _conflict(f"Entry changed after the assistant command: {after.entry_key.serialize()}")
        old = before[after.entry_key]
        replacements[after.entry_key] = replace(
            after,
            translation=old.translation,
            stage=old.stage,
            labels=old.labels,
            external_refs=old.external_refs,
            revision=after.revision.next(),
        )
    library = dict(current.label_library)
    old_library, after_library = dict(receipt.before.label_library), dict(receipt.after.label_library)
    missing = object()
    for key in old_library.keys() | after_library.keys():
        if library.get(key, missing) != after_library.get(key, missing):
            _conflict(f"Label definition changed after the assistant command: {key}")
        if key in old_library:
            library[key] = old_library[key]
        else:
            library.pop(key, None)
    candidate = tuple(replacements.get(entry.entry_key, entry) for entry in current.entries)
    removed_labels = after_library.keys() - old_library.keys()
    if any(removed_labels.intersection(entry.labels) for entry in candidate):
        _conflict("Another entry still uses a label definition that the inverse would remove.")
    return VariantChangeSet(
        current.ref, current.revision, current.source_fingerprints, candidate, tuple(library.items()), run_id
    )


def combine_variant_undo(receipts) -> VariantUndoReceipt:
    """Compose chronological receipts, rejecting intervening writes to touched values.

    Unrelated commits may advance the Variant revision between receipts. A
    repeated entry must match its entire preceding after-image, including its
    revision; repeated label definitions must match their preceding value.
    """
    receipts = tuple(receipts)
    if not receipts:
        raise ValueError("at least one undo receipt is required")
    first, last = receipts[0].before, receipts[-1].after
    initial_entries, final_entries = {}, {}
    initial_labels, final_labels = {}, {}
    missing = object()
    previous_revision = first.revision
    for receipt in receipts:
        if receipt.before.ref != first.ref or receipt.before.source_fingerprints != first.source_fingerprints:
            _conflict("Undo receipts cross Project/Variant identity or source fingerprints.")
        if receipt.before.revision < previous_revision:
            _conflict("Undo receipts must be in chronological, non-overlapping commit order.")
        if initial_labels and receipt.before.revision != previous_revision:
            _conflict("A gap after label definition edits cannot exclude an intervening library change.")
        previous_revision = receipt.after.revision
        for before, after in zip(receipt.before.entries, receipt.after.entries, strict=True):
            key = before.entry_key
            if key in final_entries and final_entries[key] != before:
                _conflict(f"An intervening entry edit breaks the undo chain: {key.serialize()}")
            initial_entries.setdefault(key, before)
            final_entries[key] = after
        old_library, new_library = dict(receipt.before.label_library), dict(receipt.after.label_library)
        for key in old_library.keys() | new_library.keys():
            before, after = old_library.get(key, missing), new_library.get(key, missing)
            if key in final_labels and final_labels[key] != before:
                _conflict(f"An intervening label definition edit breaks the undo chain: {key}")
            initial_labels.setdefault(key, before)
            final_labels[key] = after
    return capture_variant_undo(
        replace(
            first,
            entries=tuple(initial_entries.values()),
            label_library=tuple((key, value) for key, value in initial_labels.items() if value is not missing),
        ),
        replace(
            last,
            entries=tuple(final_entries.values()),
            label_library=tuple((key, value) for key, value in final_labels.items() if value is not missing),
        ),
    )


def apply_variant_undo(lifecycle, receipt: VariantUndoReceipt, context):
    """Commit the inverse to the active working copy; caller owns durable saving."""
    try:
        captured = lifecycle.active_variant_snapshot()
        if captured is None:
            _conflict("The original Variant is not active.")
        current, project_revision = captured
        change_set = build_variant_inverse(current, receipt, run_id=context.run_id or "")
        return lifecycle.commit_active_variant(change_set, context, expected_project_revision=project_revision)
    except Exception as exc:
        return OperationResult.from_exception(exc, run_id=context.run_id)
