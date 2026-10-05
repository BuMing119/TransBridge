"""Apply one authoritative state index across slots without recreating unchanged entries."""

from dataclasses import replace

from transbridge.application.io.identity import EntryRevision, ExternalEntryRef, Provenance
from transbridge.application.projections.models import copy_projection_value
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


class EntryProjectionUpdate:
    def __init__(self, states):
        self._states = {(item["entry_key"]["namespace"], item["entry_key"]["local_key"]): item for item in states}

    @classmethod
    def from_snapshot(cls, snapshot, *, keys=None):
        if snapshot is None:
            raise ValueError("工程权威快照不可用，请重新加载工程后重试。")
        states = snapshot.values.get("entries", ())
        if keys is not None:
            identities = {(key.namespace.value, key.local_key) for key in keys}
            states = (
                state
                for state in states
                if (state["entry_key"]["namespace"], state["entry_key"]["local_key"]) in identities
            )
        return cls(states)

    def entry(self, entry, *, reuse_unchanged=True):
        state = self._states.get((entry.identity.namespace.value, entry.identity.local_key))
        if state is None:
            return entry
        inferred = set(str(value) for value in state.get("inferred_fields", ()))
        values = {
            "translation": str(state.get("translation", "")),
            "stage": int(state.get("stage", 0)),
            "external_refs": (
                entry.external_refs
                if "external_refs" in inferred or "external_refs" not in state
                else tuple(
                    ExternalEntryRef.from_dict(copy_projection_value(value)) for value in state.get("external_refs", ())
                )
            ),
            "revision": entry.revision if "revision" not in state else EntryRevision(int(state["revision"])),
            "provenance": (
                entry.provenance
                if "provenance" not in state
                else tuple(Provenance.from_dict(copy_projection_value(value)) for value in state.get("provenance", ()))
            ),
        }
        changes = {name: value for name, value in values.items() if getattr(entry, name) != value}
        return replace(entry, **changes) if changes or not reuse_unchanged else entry

    def collection(self, collection):
        entries = []
        changed = False
        for entry in collection:
            projected = self.entry(entry)
            changed |= projected is not entry
            entries.append(projected)
        return TranslationEntryCollection(entries) if changed else collection
