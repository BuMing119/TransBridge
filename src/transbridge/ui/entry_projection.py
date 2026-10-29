"""Apply one authoritative state index across slots without recreating unchanged entries."""

from dataclasses import dataclass, replace

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance
from transbridge.application.projections.models import copy_projection_value
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


class EntryProjectionUpdate:
    def __init__(self, states):
        self._states = {EntryKey.from_dict(item["entry_key"]): item for item in states}

    @classmethod
    def from_snapshot(cls, snapshot, *, keys=None):
        if snapshot is None:
            raise ValueError("工程权威快照不可用，请重新加载工程后重试。")
        states = snapshot.values.get("entries", ())
        if keys is not None:
            identities = set(keys)
            states = (state for state in states if EntryKey.from_dict(state["entry_key"]) in identities)
        return cls(states)

    def entry(self, entry, *, reuse_unchanged=True):
        values = self.projected_values(entry.identity, entry)
        if not values:
            return entry
        changes = {name: value for name, value in values.items() if getattr(entry, name) != value}
        return replace(entry, **changes) if changes or not reuse_unchanged else entry

    def saved_rows(self):
        """Reuse canonical keys when projecting rows without available hydration."""
        return self._states.items()

    def is_tombstone(self, identity):
        return bool(self._states.get(identity, {}).get("tombstone", False))

    def projected_values(self, identity, source):
        """Resolve one immutable hydration input before constructing its UI entry."""
        state = self._states.get(identity)
        if state is None:
            return {}
        inferred = set(str(value) for value in state.get("inferred_fields", ()))
        return {
            "translation": str(state.get("translation", "")),
            "stage": int(state.get("stage", 0)),
            "external_refs": (
                source.external_refs
                if "external_refs" in inferred or "external_refs" not in state
                else tuple(
                    ExternalEntryRef.from_dict(copy_projection_value(value)) for value in state.get("external_refs", ())
                )
            ),
            "revision": source.revision if "revision" not in state else EntryRevision(int(state["revision"])),
            "provenance": (
                source.provenance
                if "provenance" not in state
                else tuple(Provenance.from_dict(copy_projection_value(value)) for value in state.get("provenance", ()))
            ),
        }

    def collection(self, collection):
        entries = []
        changed = False
        for entry in collection:
            projected = self.entry(entry)
            changed |= projected is not entry
            entries.append(projected)
        return TranslationEntryCollection(entries) if changed else collection


def sync_projected_entries(slots, previous, current) -> bool:
    """Advance installed entries from the previous authority without losing drafts.

    The caller guarantees the same installed Project/Variant. Translation and
    stage advance together only while matching the previous authority. Identity
    metadata reconciles independently, so genuine uncommitted UI changes remain
    visible to the save guard. Rebuild each affected index once per batch.
    """
    return bool(prepare_projected_entries(slots, previous, current).apply())


def prepare_projected_entries(slots, previous, current):
    """Build data-only replacements off the Qt thread; validate before publishing."""
    slots = tuple(slots)
    before = tuple(previous.values.get("entries", ()))
    before_by_key = None
    changes = {}
    for index, state in enumerate(current.values.get("entries", ())):
        old = before[index] if index < len(before) else None
        if old is None or old["entry_key"] != state["entry_key"]:
            if before_by_key is None:
                before_by_key = {EntryKey.from_dict(item["entry_key"]): item for item in before}
            old = before_by_key.get(EntryKey.from_dict(state["entry_key"]))
        if old is None or old == state:
            continue
        changes[EntryKey.from_dict(state["entry_key"])] = _EntryProjectionChange.capture(old, state)
    replacements = []
    for slot in slots if changes else ():
        original = slot.collection
        before_entries = tuple(original)
        entries = []
        expected_edits = []
        for entry in before_entries:
            change = changes.get(entry.identity)
            if change is not None:
                editable = _editable_fields(entry)
                projected = change.entry(entry, editable=editable)
                if projected is not entry:
                    expected_edits.append((entry, editable))
                entry = projected
            entries.append(entry)
        if expected_edits:
            replacements.append((
                slot,
                original,
                before_entries,
                tuple(expected_edits),
                TranslationEntryCollection(entries),
            ))
    return PreparedEntryProjection(previous, slots, tuple(replacements))


@dataclass(frozen=True, slots=True)
class PreparedEntryProjection:
    previous: object
    slots: tuple
    replacements: tuple

    def matches(self, slots, previous):
        slots = tuple(slots)
        return (
            len(slots) == len(self.slots)
            and all(before is after for before, after in zip(self.slots, slots, strict=True))
            and self.previous.values.get("entries", ()) == previous.values.get("entries", ())
        )

    def apply(self) -> bool | None:
        # A worker can race a genuine edit, slot replacement or in-place legacy
        # mutation. Check every input before publishing even the first slot.
        for slot, original, entries, edits, _ in self.replacements:
            if slot.collection is not original or len(original) != len(entries):
                return None
            if any(before is not after for before, after in zip(entries, original, strict=True)):
                return None
            if any(_editable_fields(entry) != expected for entry, expected in edits):
                return None
        for slot, _, _, _, collection in self.replacements:
            slot.collection = collection
        return bool(self.replacements)


def _editable_fields(entry):
    return entry.original, entry.context, entry.translation, entry.stage


@dataclass(frozen=True, slots=True)
class _EntryProjectionChange:
    content: tuple | None
    references: tuple | None
    provenance: tuple | None
    revision: int | None

    @classmethod
    def capture(cls, before, after):
        return cls(
            _state_pair(
                before,
                after,
                ("translation", "stage"),
                lambda state: (
                    str(state["translation"]),
                    int(state["stage"]),
                ),
            ),
            _state_pair(
                before,
                after,
                ("external_refs",),
                lambda state: tuple(
                    ExternalEntryRef.from_dict(copy_projection_value(value)) for value in state["external_refs"]
                ),
            ),
            _state_pair(
                before,
                after,
                ("provenance",),
                lambda state: tuple(
                    Provenance.from_dict(copy_projection_value(value)) for value in state["provenance"]
                ),
            ),
            int(after["revision"])
            if "revision" in after and "revision" not in after.get("inferred_fields", ())
            else None,
        )

    def entry(self, entry, *, editable):
        values = {}
        content = (editable[2] or "", editable[3])
        if self.content is not None and content == self.content[0] and content != self.content[1]:
            values.update(zip(("translation", "stage"), self.content[1], strict=True))
        if self.references is not None and entry.external_refs == self.references[0]:
            if entry.external_refs != self.references[1]:
                values["external_refs"] = self.references[1]
        content_current = (
            self.content is not None
            and (values.get("translation", content[0]), values.get("stage", content[1])) == self.content[1]
        )
        if content_current and self.provenance is not None and entry.provenance == self.provenance[0]:
            if entry.provenance != self.provenance[1]:
                values["provenance"] = self.provenance[1]
        references_current = self.references is None or (
            values.get("external_refs", entry.external_refs) == self.references[1]
        )
        if self.revision is not None and (values or (content_current and references_current)):
            revision = EntryRevision(max(entry.revision.value, self.revision))
            if revision != entry.revision:
                values["revision"] = revision
        # Copy the captured mutable source fields, not a mixture of values read
        # before and after an in-place edit during worker preparation.
        if values:
            values.setdefault("translation", editable[2])
            values.setdefault("stage", editable[3])
            return replace(entry, original=editable[0], context=editable[1], **values)
        return entry


def _state_pair(before, after, fields, decode):
    for state in (before, after):
        if any(name not in state or name in state.get("inferred_fields", ()) for name in fields):
            return None
    return decode(before), decode(after)
