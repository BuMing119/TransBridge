# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Complete frontend rows for the active Workbench source and its display order."""

from __future__ import annotations

from dataclasses import dataclass, replace

from transbridge.ui.project_labels import entry_label_key

from .filters_presenter import FiltersPresenter
from .translation_table_sorting import ordered_source_rows


@dataclass(frozen=True, slots=True)
class ResidentTableRows:
    entries: tuple
    identities: tuple
    positions: dict
    key_positions: dict

    @classmethod
    def index(cls, entries):
        identities = tuple(entry_label_key(entry) for entry in entries)
        return cls(
            entries,
            identities,
            {key: row for row, key in enumerate(identities)},
            {entry.identity: row for row, entry in enumerate(entries)},
        )

    def get(self, row):
        return self.entries[row] if 0 <= row < len(self.entries) else None


@dataclass(frozen=True, slots=True)
class RangeResult:
    rows: ResidentTableRows
    labels: dict
    marked: tuple

    @property
    def entries(self):
        return self.rows.entries

    @property
    def identities(self):
        return self.rows.identities


def load_resident_rows(rows, cancelled):
    if cancelled.is_set():
        return None
    entries, labels, marked = [], {}, []
    for entry, key, membership in rows:
        if cancelled.is_set():
            return None
        entries.append(entry)
        if membership:
            labels[key] = membership
            marked.append(entry)
    if not cancelled.is_set():
        return RangeResult(ResidentTableRows.index(tuple(entries)), labels, tuple(marked))
    return None


def query_resident_rows(source, state, sort, cancelled):
    def entries():
        for entry in source.entries:
            if cancelled.is_set():
                return
            yield entry

    filters = FiltersPresenter()
    filters.update(state)
    matched = filters.apply(entries(), source.labels)
    if cancelled.is_set():
        return None
    order = ordered_source_rows(matched, source.labels, *sort)
    return RangeResult(ResidentTableRows.index(tuple(matched[row] for row in order)), source.labels, source.marked)


def patch_resident_rows(source, changes):
    if changes is None:
        return None
    updated, labels = {}, None
    for state in changes:
        position = source.rows.key_positions.get(state.entry_key)
        if (position is None) != state.tombstone:
            return None
        if position is None:
            continue
        entry = source.entries[position]
        candidate = replace(
            entry,
            translation=state.translation,
            stage=state.stage.value,
            revision=state.revision,
            provenance=state.provenance,
            external_refs=state.external_refs,
        )
        if candidate != entry:
            updated[position] = candidate
        key = source.identities[position]
        if set(state.labels) != source.labels.get(key, set()):
            if labels is None:
                labels = dict(source.labels)
            if state.labels:
                labels[key] = set(state.labels)
            else:
                labels.pop(key, None)
    if not updated and labels is None:
        return source
    entries = list(source.entries)
    for position, entry in updated.items():
        entries[position] = entry
    labels = source.labels if labels is None else labels
    marked = tuple(entries[position] for position in sorted(source.rows.positions[key] for key in labels))
    return RangeResult(replace(source.rows, entries=tuple(entries)), labels, marked)
