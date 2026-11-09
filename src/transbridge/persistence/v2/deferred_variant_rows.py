# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""A revision-bound entry range with a sparse in-process edit overlay."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from copy import copy
from itertools import islice
from typing import TYPE_CHECKING

from transbridge.application.io.identity import EntryKey

if TYPE_CHECKING:
    from .variant import VariantEntryState


class DeferredVariantRows:
    def __init__(self, read_range, read_entries, rebase, *, preview=(), changes=()) -> None:
        self._read_range = read_range
        self._read_entries = read_entries
        self._rebase = rebase
        self._preview = preview
        self._known = {entry.entry_key: entry for entry in preview}
        self._changes = {entry.entry_key: entry for entry in changes}
        self._baseline = object()
        self._previous_commit: tuple[object, tuple[VariantEntryState, ...]] | None = None

    def __iter__(self) -> Iterator[VariantEntryState]:
        return (self._changes.get(entry.entry_key, entry) for entry in self._read_range())

    def for_sources(self, namespaces):
        return (
            self._changes.get(entry.entry_key, entry)
            for namespace in sorted(namespaces)
            for entry in self._read_range(namespace)
        )

    def __getitem__(self, index):
        if isinstance(index, int) and index >= 0:
            if index < len(self._preview):
                return self.overlay(self._preview[index])
            entry = next(islice(self, index, index + 1), None)
            if entry is None:
                raise IndexError(index)
            return entry
        if isinstance(index, slice) and (index.start or 0) >= 0 and (index.stop or 0) >= 0 and (index.step or 1) > 0:
            return tuple(islice(self, index.start, index.stop, index.step))
        return tuple(self)[index]

    def get(self, key: EntryKey) -> VariantEntryState | None:
        cached = self._changes.get(key) or self._known.get(key)
        return cached if cached is not None else next(iter(self._read_entries((key,))), None)

    def get_many(self, keys: Iterable[EntryKey]) -> tuple[VariantEntryState, ...]:
        requested = tuple(dict.fromkeys(keys))
        found = {}
        missing = []
        for key in requested:
            cached = self._changes.get(key) or self._known.get(key)
            if cached is None:
                missing.append(key)
            else:
                found[key] = cached
        if missing:
            found.update((entry.entry_key, entry) for entry in self._read_entries(missing))
        return tuple(found[key] for key in requested if key in found)

    def preview(self) -> tuple[VariantEntryState, ...]:
        rows = {entry.entry_key: entry for entry in self._preview}
        rows.update(self._changes)
        return tuple(rows.values())

    def overlay(self, entry: VariantEntryState) -> VariantEntryState:
        return self._changes.get(entry.entry_key) or self._known.get(entry.entry_key, entry)

    def pending(self) -> tuple[VariantEntryState, ...]:
        return tuple(self._changes.values())

    def changed_from(self, previous: DeferredVariantRows) -> tuple[VariantEntryState, ...]:
        return tuple(entry for key, entry in self._changes.items() if previous._changes.get(key) != entry)

    def committed_from(self, previous: DeferredVariantRows) -> tuple[VariantEntryState, ...] | None:
        """Return the saved delta when the previous view has a known baseline."""
        if self._baseline is previous._baseline:
            return ()
        if self._previous_commit is not None and self._previous_commit[0] is previous._baseline:
            return self._previous_commit[1]
        return None

    def patched(self, entries: Iterable[VariantEntryState]) -> DeferredVariantRows:
        rows = copy(self)
        rows._changes = self._changes | {entry.entry_key: entry for entry in entries}
        return rows

    def rebased(
        self, project_revision: int, variant_revision: int, *, keep_changes: bool = False
    ) -> DeferredVariantRows:
        known = self._preview if keep_changes else tuple(self.overlay(entry) for entry in self._preview)
        base = self._rebase(project_revision, variant_revision, known)
        if keep_changes:
            base._baseline = self._baseline
            base._previous_commit = self._previous_commit
            return base.patched(self._changes.values())
        base._previous_commit = self._baseline, self.pending()
        return base
