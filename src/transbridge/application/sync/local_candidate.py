"""Build atomic local candidates without losing duplicate or deferred entries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from transbridge.application.io.identity import EntryRevision

from .candidate_matching import (
    consistent_original_identity,
    is_content_source,
    is_translation_source,
    journal_record,
    stage_from_agreement,
    translation_source_with_stage,
    without_remote_identity,
)
from .models import EntrySummary, LocalEntrySnapshot, RemoteEntrySnapshot, SyncAction, SyncPlanItem


class RemoteSourceIndex:
    """Find the exact reviewed source rather than the last record with its key."""

    def __init__(self, entries: tuple[RemoteEntrySnapshot, ...]) -> None:
        self._by_summary = defaultdict(list)
        self._by_content = defaultdict(list)
        self._by_translation = defaultdict(list)
        self._empty_original_hash = None
        for entry in entries:
            summary = EntrySummary.from_remote(entry)
            self._by_summary[(summary, entry.external_ref)].append(entry)
            self._by_content[_content_scope(entry.entry_key), without_remote_identity(summary)].append(entry)
            if entry.translation and not entry.deleted and consistent_original_identity(entry):
                if not entry.original:
                    self._empty_original_hash = summary.original_hash
                self._by_translation[
                    entry.entry_key.namespace,
                    summary.original_hash,
                    summary.translation_hash,
                    entry.entry_key.local_key if not entry.original else None,
                ].append(entry)
        self._translation_stages = {}
        for key, entries in self._by_translation.items():
            stages = {entry.stage for entry in entries}
            self._translation_stages[key] = next(iter(stages)) if len(stages) == 1 else None

    def get_for_item(self, item: SyncPlanItem) -> RemoteEntrySnapshot:
        if is_translation_source(item.after):
            return self._translation_for_item(item)
        if is_content_source(item.after):
            matches = self._by_content.get((_content_scope(item.entry_key), item.after), ())
            if len(matches) < 2 or matches[0].deleted or item.external_ref is not None:
                raise RuntimeError("the reviewed equivalent remote content is unavailable")
            # Every member was indexed by identical content. Reuse it without
            # rescanning a quest, and never copy a member's arbitrary remote ID.
            return replace(
                matches[0],
                entry_key=item.entry_key,
                remote_revision=item.after.revision,
                external_ref=None,
                context_order=None,
            )
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

    def _translation_for_item(self, item):
        summary = item.after
        key = (
            item.entry_key.namespace,
            summary.original_hash,
            summary.translation_hash,
            item.entry_key.local_key if summary.original_hash == self._empty_original_hash else None,
        )
        matches = self._by_translation.get(key, ())
        if not matches or item.external_ref is not None:
            raise RuntimeError("the reviewed remote translation content is unavailable")
        expected_stage = stage_from_agreement(
            self._translation_stages[key],
            current_matches=item.before is not None and item.before.translation_hash == summary.translation_hash,
            current_stage=0 if item.before is None else item.before.stage,
        )
        source = translation_source_with_stage(matches[0], item.entry_key, expected_stage)
        if EntrySummary.from_remote(source) != summary:
            raise RuntimeError("the reviewed remote translation content changed")
        return source


def _content_scope(key):
    record = journal_record(key)
    return ("journal", *record) if record is not None else ("key", key.namespace, key.local_key)


def build_local_candidate(
    current_entries: tuple[LocalEntrySnapshot, ...],
    remote_sources: RemoteSourceIndex,
    items: tuple[SyncPlanItem, ...],
    *,
    attach_remote_refs: bool = True,
    preserve_local_source: bool = False,
) -> tuple[LocalEntrySnapshot, ...]:
    """Download callers preserve source layout and omit durable remote references.

    Defaults retain the bidirectional sync contract; the remote source index
    still resolves the exact reviewed snapshot while building either candidate.
    """
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
        content_only = is_content_source(item.after)
        preserve_source = current is not None and (
            preserve_local_source or item.entry_key.original is not None or content_only
        )
        updated = LocalEntrySnapshot(
            item.entry_key,
            revision,
            current.original if preserve_source else remote.original,
            remote.translation,
            current.context if preserve_source else remote.context,
            remote.stage,
            remote.external_ref if attach_remote_refs else None,
            context_order=current.context_order
            if current is not None and (preserve_local_source or content_only)
            else remote.context_order,
        )
        if matching:
            candidate[matching[0]] = updated
        else:
            positions[item.entry_key].append(len(candidate))
            candidate.append(updated)
    return tuple(entry for index, entry in enumerate(candidate) if index not in removed)
