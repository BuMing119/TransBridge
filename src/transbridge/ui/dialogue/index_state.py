"""Identify changes that actually invalidate the task relationship index."""

from __future__ import annotations


class IndexStructure:
    def __init__(self) -> None:
        self._scope = self._source = self._snapshot = self._reason = None
        self._entries = None

    def changed(self, scope, slot, entries, reason) -> bool:
        source = None if slot is None else slot.plugin
        snapshot = None if slot is None else slot.source_snapshot
        # Translations, review stages and mutation revisions cannot change task links.
        structure = tuple(
            (entry.identity, entry.context, entry.form_id_with_plugin, entry.editor_id, entry.metadata)
            for entry in entries
        )
        changed = (
            scope != self._scope
            or source is not self._source
            or snapshot is not self._snapshot
            or reason != self._reason
            or structure != self._entries
        )
        self._scope, self._source, self._snapshot, self._reason = scope, source, snapshot, reason
        self._entries = structure
        return changed
