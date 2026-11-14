# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Workbench pages over a full-scope, revision-bound source collection."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision
from transbridge.application.projections.models import DeferredProjectRows, copy_projection_value
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.project_store.paged_reads import describe_source
from transbridge.persistence.v2.variant import VariantEntryState
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.projection_types import CollectionSlot


class ProjectRowsCollection:
    """Keep only Workbench pages; explicit iteration reads the complete source."""

    MAX_PAGES = 4

    def __init__(self, reader, source) -> None:
        self.reader = reader
        self.source = source
        self.token = reader.token
        self._variant_revision = reader.token.variant_revision
        self._revision = EntryRevision(self._variant_revision)
        self._view_update = None
        self._authority = None
        self._states = None
        self._pages = {0: source.preview} if source.preview else {}
        self._initial_count = source.count
        self._reading_order = source.info.format_id is FormatId.PLUGIN_SSE

    @property
    def collection_revision(self):
        return self._revision

    def __len__(self):
        return self._initial_count

    def __iter__(self):
        rows = self.reader.full_rows(namespace=self.source.info.namespace, token=self.token, include_removed=True)
        authority, states = self._authority, self._states
        return _translation_entries(rows, authority, states)

    def __contains__(self, key):
        return self.get(key) is not None

    def get(self, key):
        if isinstance(key, str):
            try:
                key = EntryKey.deserialize(key)
            except ValueError:
                return TranslationEntryCollection(self).get(key)
        if key.namespace != self.source.info.namespace:
            return None
        for rows in self._pages.values():
            for row in rows:
                if row.state.entry_key == key:
                    return next(_translation_entries((row,), self._authority, self._states), None)
        row = self.reader.entry(key, self.token)
        return None if row is None else next(_translation_entries((row,), self._authority, self._states), None)

    def snapshot(self, key):
        entry = self.get(key)
        return None if entry is None else entry.snapshot()

    def freeze_entries(self):
        """Capture a revision-bound batch reader without doing I/O on the GUI thread."""
        store, token = self.reader.store, self.token
        namespace = self.source.info.namespace
        authority = self._authority
        states = None if self._states is None else dict(self._states)

        def read(keys):
            selected = tuple(key for key in keys if key.namespace == namespace)
            if not selected:
                return ()
            rows = store.read_entries(token.variant_id, selected, token=token)
            return tuple(_translation_entries(rows, authority, states))

        return read

    def get_by_id(self, entry_id):
        return TranslationEntryCollection(self).get_by_id(entry_id)

    def get_by_key(self, local_key):
        return TranslationEntryCollection(self).get_by_key(local_key)

    def get_by_external_ref(self, reference):
        return tuple(
            entry for entry in self if any(item.index_key == reference.index_key for item in entry.external_refs)
        )

    def to_json(self, *args, **options):
        return TranslationEntryCollection(self).to_json(*args, **options)

    def apply(self, *_args, **_options):
        raise TypeError("Project rows are a read-only view; submit a project edit command")

    add = apply
    remove = apply

    def native_template(self):
        return self.reader.template(self.source, self.token)

    def statistics(self):
        authority = self._authority
        if self._states is None:
            return self.reader.store.scope_statistics(
                self.token, self.source.info.namespace, () if authority is None else authority.pending()
            )
        return Counter(
            (entry.context or "", entry.stage, bool(entry.stage >= 1 and entry.translation), tuple(sorted(labels)))
            for entry, _key, labels in self.query_rows()
        )

    def visible_rows(self):
        rows = (row for offset in sorted(self._pages) for row in self._pages[offset])
        return tuple(_translation_entries(rows, self._authority, self._states))

    def logical_rows(self):
        rows = {}
        for offset, page in sorted(self._pages.items()):
            for index, row in enumerate(page, offset):
                entry = next(_translation_entries((row,), self._authority, self._states), None)
                if entry is not None:
                    rows[index] = entry
        return rows

    def labels(self):
        result = {}
        for rows in self._pages.values():
            for row in rows:
                state = _resolved_state(row.state, self._authority, self._states)
                if state is not None and state.labels:
                    key = (
                        state.entry_key.serialize() if state.entry_key.original is not None else row.snapshot.legacy_id
                    )
                    result[key] = set(state.labels)
        return result

    def query_rows(self):
        rows = self.reader.full_rows(
            namespace=self.source.info.namespace,
            token=self.token,
            include_removed=True,
            reading_order=self._reading_order,
        )
        return self._rows_with_labels(rows, self._authority, self._states)

    def _rows_with_labels(self, rows, authority, states):
        for row in rows:
            state = _resolved_state(row.state, authority, states)
            if state is not None and not state.tombstone:
                entry = _translation_entry(row.snapshot, state)
                yield entry, entry_label_key(entry), set(state.labels)

    def label_changes(self, provided, library, *, removing_library_labels=False):
        rows = (row for page in self._pages.values() for row in page)
        visible = tuple(self._rows_with_labels(rows, self._authority, self._states))
        visible_keys = {key for _entry, key, _labels in visible}
        rows = self.query_rows() if removing_library_labels or provided.keys() - visible_keys else visible
        changes = {}
        for entry, key, labels in rows:
            next_labels = set(provided.get(key, ())) if key in visible_keys or key in provided else labels
            next_labels.intersection_update(library)
            if next_labels != labels:
                changes[entry.identity] = next_labels
        return changes

    def view_updates_from(self, revision):
        if self._view_update is not None and revision == self._view_update[0]:
            return self._view_update[1]
        return None

    def sync(self, values, authority, states, committed, updates) -> bool:
        token = replace(
            self.token,
            project_revision=values["persisted_project_revision"],
            variant_revision=values["persisted_variant_revision"],
        )
        persisted_changed = token.variant_revision != self.token.variant_revision
        if persisted_changed and (committed is None or any(state.tombstone for state in committed.values())):
            self._pages.clear()
        elif committed:
            self._pages = {
                offset: tuple(
                    replace(row, state=committed[row.state.entry_key]) if row.state.entry_key in committed else row
                    for row in rows
                )
                for offset, rows in self._pages.items()
            }
        variant_revision = values["variant_revision"]
        changed = (
            variant_revision != self._variant_revision
            or bool(updates)
            or (updates is None and (persisted_changed or authority is not self._authority))
        )
        if changed:
            self._view_update = self._revision, None if updates is None else tuple(updates.values())
            self._revision = EntryRevision(max(self._revision.value + 1, variant_revision))
        self._variant_revision = variant_revision
        self.token = token
        self._authority = authority
        self._states = states
        return changed

    def missing_pages(self, offsets):
        return tuple(offset for offset in offsets if offset not in self._pages)

    def read_page(self, offset, token):
        return self.reader.rows(
            namespace=self.source.info.namespace,
            offset=offset,
            token=token,
            reading_order=self._reading_order,
        )

    def install_pages(self, pages, token, requested):
        if token != self.token:
            return False
        self._pages.update(pages)
        limit = max(self.MAX_PAGES, len(requested))
        if len(self._pages) > limit:
            anchor = requested[0]
            kept = sorted(self._pages, key=lambda offset: (offset not in requested, abs(offset - anchor)))[:limit]
            self._pages = {offset: self._pages[offset] for offset in kept}
        return True


def sync_paged_collections(collections, snapshot, previous):
    values = snapshot.values
    rows = values["entries"]
    authority, states = None, None
    if isinstance(rows, DeferredProjectRows):
        authority = rows.entries
    else:
        decoded = (VariantEntryState.from_dict(copy_projection_value(row)) for row in rows)
        states = {entry.entry_key: entry for entry in decoded}
    before = None if previous is None else previous.values["entries"]
    committed, updates = _paged_changes(authority, before)
    changed = False
    for collection in collections:
        namespace = collection.source.info.namespace
        changed |= collection.sync(
            values,
            authority,
            states,
            None if committed is None else committed.get(namespace, {}),
            None if updates is None else updates.get(namespace, {}),
        )
    return changed


def _paged_changes(authority, before):
    if before is None:
        return {}, {}
    if authority is None or not isinstance(before, DeferredProjectRows):
        return None, None
    previous = before.entries
    if previous is authority:
        return {}, {}
    saved = authority.committed_from(previous)
    if saved is None:
        return None, None
    committed, updates = defaultdict(dict), defaultdict(dict)
    for state in saved:
        committed[state.entry_key.namespace][state.entry_key] = state
    pending = {state.entry_key: state for state in previous.pending()}
    current = {state.entry_key: state for state in (*saved, *authority.pending())}
    if pending.keys() - current.keys():
        return committed, None
    for key, state in current.items():
        if state != pending.get(key):
            updates[key.namespace][key] = state
    return committed, updates


def build_paged_slots(reader):
    return tuple(
        (
            source.key,
            CollectionSlot(
                source.info.name,
                ProjectRowsCollection(reader, source),
                source_snapshot=describe_source(source.info),
                format_id=source.info.format_id,
                source_id=source.key,
                source_namespace=source.info.namespace.value,
                reference_path=source.info.reference_path,
            ),
        )
        for source in reader.sources
    )


def _resolved_state(base, authority, states):
    return (
        authority.overlay(base) if authority is not None else states.get(base.entry_key) if states is not None else base
    )


def _translation_entries(rows, authority, states):
    for row in rows:
        state = _resolved_state(row.state, authority, states)
        if state is None or state.tombstone:
            continue
        yield _translation_entry(row.snapshot, state)


def _translation_entry(item, state):
    return TranslationEntry(
        id=item.legacy_id,
        key=item.entry_key.local_key,
        original=item.original,
        translation=state.translation,
        stage=state.stage.value,
        context=item.context,
        entry_key=item.entry_key,
        external_refs=state.external_refs,
        revision=state.revision,
        provenance=state.provenance,
        metadata=item.metadata,
        string_id=item.string_id,
    )
