"""Reuse delivered authority evidence and reconcile only changed visible entries."""

from contextlib import ExitStack
from dataclasses import dataclass, field
import json
from threading import RLock

from transbridge.application.io.identity import EntryKey
from transbridge.application.projections.models import copy_projection_value


def _external_refs(values):
    return tuple(
        sorted(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in values)
    )


def _entry_state(entry):
    return entry.translation or "", entry.stage, _external_refs(value.to_dict() for value in entry.external_refs)


def _immutable(value):
    return isinstance(value, (str, int, float, bool, type(None))) or (
        isinstance(value, tuple) and all(_immutable(item) for item in value)
    )


@dataclass
class _CollectionEvidence:
    version: int | None = None
    states: dict = field(default_factory=dict)
    mutable_references: set = field(default_factory=set)


class ProjectionConsistency:
    """Cache content equality; invalidation follows collection and entry writes.

    Reference envelopes with legacy nested mutable metadata need a small fallback
    check because those objects do not pass through an entry setter. No reader
    treats dirty=False, a collection revision, or object identity as edit proof.
    """

    def __init__(self):
        self._lock = RLock()
        self._entries = None
        self._authority = {}
        self._authority_rows = {}
        self._collections = {}
        self._visible = {}
        self._mismatches = set()

    def diverged(self, snapshot, slots):
        collections = {slot.collection.change_journal: slot.collection for slot in slots}
        with self._lock, ExitStack() as locks:
            for journal in sorted(collections, key=id):
                locks.enter_context(journal.lock)
            affected = self._authority_changes(snapshot)
            for journal in self._collections.keys() - collections.keys():
                evidence = self._collections.pop(journal)
                for key in evidence.states:
                    self._remove_visible(journal, key)
                affected.update(evidence.states)
            for journal, collection in collections.items():
                evidence = self._collections.setdefault(journal, _CollectionEvidence())
                changed = journal.since(evidence.version)
                if changed is None:
                    current = {entry.identity: entry for entry in collection}
                    changed = evidence.states.keys() | current.keys()
                else:
                    changed |= evidence.mutable_references
                    current = {key: collection.get(key) for key in changed}
                for key in changed:
                    self._remove_visible(journal, key)
                    entry = current.get(key)
                    if entry is None:
                        evidence.states.pop(key, None)
                        evidence.mutable_references.discard(key)
                    else:
                        state = _entry_state(entry)
                        evidence.states[key] = state
                        self._visible.setdefault(key, {})[journal] = state
                        if all(_immutable(reference.metadata) for reference in entry.external_refs):
                            evidence.mutable_references.discard(key)
                        else:
                            evidence.mutable_references.add(key)
                evidence.version = journal.version
                affected.update(changed)
            for key in affected:
                visible = self._visible.get(key, {})
                expected = self._authority.get(key)
                matches = (
                    bool(visible) and expected is not None and all(value == expected for value in visible.values())
                )
                if matches or (not visible and key not in self._authority):
                    self._mismatches.discard(key)
                else:
                    self._mismatches.add(key)
            return bool(self._mismatches)

    def _remove_visible(self, journal, key):
        owners = self._visible.get(key)
        if owners is not None:
            owners.pop(journal, None)
            if not owners:
                self._visible.pop(key)

    def _authority_changes(self, snapshot):
        entries = () if snapshot is None else snapshot.values.get("entries", ())
        if entries is self._entries:
            return set()
        rows = {EntryKey.from_dict(item["entry_key"]): item for item in entries if not item.get("tombstone", False)}
        authority = {}
        for key, item in rows.items():
            authority[key] = (
                self._authority[key]
                if item is self._authority_rows.get(key)
                else (
                    str(item.get("translation", "")),
                    int(item.get("stage", 0)),
                    _external_refs(copy_projection_value(value) for value in item.get("external_refs", ())),
                )
            )
        changed = {
            key for key in authority.keys() | self._authority.keys() if authority.get(key) != self._authority.get(key)
        }
        self._entries, self._authority = entries, authority
        self._authority_rows = rows
        return changed
