"""Identify changes that actually invalidate the task relationship index."""

from __future__ import annotations


class IndexStructure:
    def __init__(self) -> None:
        self._scope = self._source = self._snapshot = self._reason = None
        self._collection = self._version = None
        self._entries = {}

    def source_changed(self, scope, slot, reason) -> bool:
        """Invalidate source ownership without walking an unused collection."""
        source = None if slot is None else slot.plugin
        snapshot = None if slot is None else slot.source_snapshot
        changed = (
            scope != self._scope
            or source is not self._source
            or snapshot is not self._snapshot
            or reason != self._reason
        )
        self._scope, self._source, self._snapshot, self._reason = scope, source, snapshot, reason
        if changed:
            self._collection = self._version = None
            self._entries = {}
        return changed

    def changed(self, collection) -> bool:
        """Compare relationship fields, using bounded deltas for ordinary edits."""
        journal = collection.change_journal
        with journal.lock:
            keys = None if collection is not self._collection else journal.since(self._version)
            if keys is None:
                entries = {entry.identity: self._structure(entry) for entry in collection}
                changed = entries != self._entries or tuple(entries) != tuple(self._entries)
                self._entries = entries
            else:
                changed = False
                for key in keys:
                    entry = collection.get(key)
                    structure = None if entry is None else self._structure(entry)
                    if structure != self._entries.get(key):
                        changed = True
                if changed:
                    self._entries = {entry.identity: self._structure(entry) for entry in collection}
            self._collection, self._version = collection, journal.version
        return changed

    @property
    def keys(self):
        return tuple(self._entries)

    @staticmethod
    def _structure(entry):
        # Translations, review stages and mutation revisions cannot change task links.
        return entry.context, entry.form_id_with_plugin, entry.editor_id, entry.metadata
