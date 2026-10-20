"""Build atomic local candidates without losing duplicate or deferred entries."""

from __future__ import annotations

from collections import defaultdict

from transbridge.application.io.identity import EntryRevision

from .models import EntrySummary, LocalEntrySnapshot, RemoteEntrySnapshot, SyncAction, SyncPlanItem


class RemoteSourceIndex:
    """Find the exact reviewed source rather than the last record with its key."""

    def __init__(self, entries: tuple[RemoteEntrySnapshot, ...]) -> None:
        self._by_summary = defaultdict(list)
        for entry in entries:
            self._by_summary[(EntrySummary.from_remote(entry), entry.external_ref)].append(entry)

    def get_for_item(self, item: SyncPlanItem) -> RemoteEntrySnapshot:
        matches = self._by_summary.get((item.after, item.external_ref), ())
        if not matches:
            raise RuntimeError("the reviewed remote source is unavailable")
        if len(matches) > 1:
            keyed = tuple(entry for entry in matches if entry.entry_key == item.entry_key)
            if keyed:
                matches = keyed
        if len(matches) > 1 and any(entry != matches[0] for entry in matches[1:]):
            raise RuntimeError("the reviewed remote source is ambiguous")
        return matches[0]


def build_local_candidate(
    current_entries: tuple[LocalEntrySnapshot, ...],
    remote_sources: RemoteSourceIndex,
    items: tuple[SyncPlanItem, ...],
) -> tuple[LocalEntrySnapshot, ...]:
    positions = defaultdict(list)
    candidate = list(current_entries)
    for index, entry in enumerate(candidate):
        positions[entry.entry_key].append(index)
    removed = set()
    for item in items:
        matching = positions.get(item.entry_key, ())
        if len(matching) > 1:
            raise RuntimeError("a local mutation cannot target a duplicate entry identity")
        current = candidate[matching[0]] if matching else None
        if item.action is SyncAction.DELETE_LOCAL:
            if matching:
                removed.add(matching[0])
            continue
        remote = remote_sources.get_for_item(item)
        if remote.deleted:
            raise RuntimeError("remote source entry is deleted")
        revision = EntryRevision() if current is None else current.revision.next()
        updated = LocalEntrySnapshot(
            item.entry_key,
            revision,
            current.original if current is not None and item.entry_key.original is not None else remote.original,
            remote.translation,
            current.context if current is not None and item.entry_key.original is not None else remote.context,
            remote.stage,
            remote.external_ref,
            context_order=remote.context_order,
        )
        if matching:
            candidate[matching[0]] = updated
        else:
            positions[item.entry_key].append(len(candidate))
            candidate.append(updated)
    return tuple(entry for index, entry in enumerate(candidate) if index not in removed)
