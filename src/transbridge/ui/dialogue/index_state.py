# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Identify changes that actually invalidate the task relationship index."""

from __future__ import annotations

from transbridge.ui.project_rows import ProjectRowsCollection


class IndexStructure:
    def __init__(self) -> None:
        self._scope = self._source = self._snapshot = self._reason = None
        self._reading_entries = None
        self._collection = self._version = None
        self._entries = {}
        self.removed_keys = None

    def source_changed(self, scope, slot, reason) -> bool:
        """Invalidate source ownership without walking an unused collection."""
        source = None if slot is None else slot.plugin
        snapshot = None if slot is None else slot.source_snapshot
        reading_entries = getattr(slot, "reading_source_entries", None)
        changed = (
            scope != self._scope
            or source is not self._source
            or snapshot is not self._snapshot
            or reading_entries is not self._reading_entries
            or reason != self._reason
        )
        self._scope, self._source, self._snapshot, self._reason = scope, source, snapshot, reason
        self._reading_entries = reading_entries
        if changed:
            self._collection = self._version = None
            self._entries = {}
        return changed

    def changed(self, collection) -> bool:
        """Compare relationship fields, using bounded deltas for ordinary edits."""
        if isinstance(collection, ProjectRowsCollection):
            return self._paged_changed(collection)
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

    def _paged_changed(self, collection: ProjectRowsCollection) -> bool:
        revision = collection.collection_revision
        if collection is self._collection and revision == self._version:
            return False
        updates = collection.view_updates_from(self._version) if collection is self._collection else None
        self._collection, self._version = collection, revision
        self._entries = {}
        self.removed_keys = None if updates is None else frozenset(row.entry_key for row in updates if row.tombstone)
        # Sparse translation, label and external-reference changes leave the
        # source's task relationships intact.
        return self.removed_keys is None or bool(self.removed_keys)

    @property
    def keys(self):
        return tuple(self._entries)

    @staticmethod
    def _structure(entry):
        # Translations, review stages and mutation revisions cannot change task links.
        return entry.context, entry.form_id_with_plugin, entry.editor_id, entry.metadata
