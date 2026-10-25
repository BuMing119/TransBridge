"""Plan source-isolated downloads and rebase accepted updates on current edits."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.paratranz_context_order import context_orders
from transbridge.converter.translation_entry import TranslationEntry

from .download_review import DownloadDecision
from .local_candidate import RemoteSourceIndex, build_local_candidate
from .models import (
    ConflictPolicy,
    DeletionPolicy,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlan,
)
from .planner import SyncPlanner


@dataclass(frozen=True, slots=True)
class BatchDownloadSource:
    source_id: str
    label: str
    local_entries: tuple[LocalEntrySnapshot, ...]
    remote_entries: tuple[RemoteEntrySnapshot, ...]

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("download source requires a stable source ID")
        namespaces = {entry.entry_key.namespace for entry in self.local_entries}
        if len(namespaces) > 1:
            raise ValueError("download source contains multiple source identities")
        if any(entry.entry_key.namespace not in namespaces for entry in self.remote_entries):
            raise ValueError("download source contains another source's remote entries")


@dataclass(frozen=True, slots=True)
class BatchDownloadPlan:
    sources: tuple[BatchDownloadSource, ...]
    plans: tuple[SyncPlan, ...]
    cleared_decisions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.sources) != len(self.plans):
            raise ValueError("each download source must have one plan")

    @property
    def updated_count(self) -> int:
        return sum(item.action is SyncAction.UPDATE_LOCAL for plan in self.plans for item in plan.items)

    @property
    def deferred_count(self) -> int:
        return sum(plan.conflicts for plan in self.plans)


@dataclass(frozen=True, slots=True)
class BatchDownloadCandidates:
    """Only ``updated_keys`` may be submitted; all other current state is retained."""

    entries: tuple[LocalEntrySnapshot, ...]
    updated_keys: tuple[EntryKey, ...]
    deferred_keys: tuple[EntryKey, ...]
    replan_source_ids: tuple[str, ...]


def snapshots_from_entries(entries: Sequence[TranslationEntry]) -> tuple[LocalEntrySnapshot, ...]:
    """Capture source order and current state without historical cloud associations."""
    entries = tuple(entries)
    return tuple(
        LocalEntrySnapshot(
            entry.identity,
            entry.revision,
            entry.original,
            entry.translation or "",
            entry.context or "",
            entry.stage,
            context_order=order,
        )
        for entry, order in zip(entries, context_orders(entries), strict=True)
    )


def plan_batch_download(
    sources: Sequence[BatchDownloadSource],
    *,
    policy: ConflictPolicy = ConflictPolicy.PREFER_REMOTE,
    decisions: tuple[DownloadDecision, ...] = (),
    review_keys: tuple[EntryKey, ...] = (),
) -> BatchDownloadPlan:
    """Drop only stale review choices; independent valid choices remain effective."""
    sources = tuple(sources)
    _validate_sources(sources)
    choices = {decision.row_id: decision for decision in decisions}
    if len(choices) != len(decisions):
        raise ValueError("download decisions contain duplicate rows")
    planner = SyncPlanner(local_state_only=True, preserve_local_source=True, content_download=True)
    plans, retained = [], set()
    for source in sources:
        scope = f"paratranz:batch-download:source:{source.source_id}"
        options = dict(
            operation=SyncOperation.DOWNLOAD,
            conflict_policy=policy,
            deletion_policy=DeletionPolicy.PRESERVE,
            scope=scope,
            download_review_keys=review_keys,
        )
        baseline = planner.plan(source.local_entries, source.remote_entries, **options)
        valid = []
        for row in baseline.download_review:
            decision = choices.get(row.row_id)
            if decision is None:
                continue
            if decision.choice_token == "keep_local" or (
                row.allow_remote_choice
                and any(
                    candidate.token == decision.choice_token and candidate.selectable for candidate in row.candidates
                )
            ):
                valid.append(decision)
                retained.add(row.row_id)
        plans.append(
            planner.plan(source.local_entries, source.remote_entries, download_decisions=tuple(valid), **options)
            if valid
            else baseline
        )
    return BatchDownloadPlan(sources, tuple(plans), tuple(sorted(set(choices) - retained)))


def rebased_candidates(
    preview: BatchDownloadPlan, current_sources: Sequence[BatchDownloadSource]
) -> BatchDownloadCandidates:
    """Accept unchanged targets while preserving unrelated and concurrent local edits.

    Revision-only churn is harmless. Changed source text or membership requires a
    fresh source plan; edits to a target translation defer only that target.
    """
    current_sources = tuple(current_sources)
    _validate_sources(current_sources)
    current = {source.source_id: source for source in current_sources}
    replacements, updated, deferred, replan = {}, [], [], []
    for source, plan in zip(preview.sources, preview.plans, strict=True):
        fresh = current.get(source.source_id)
        if fresh is None or _source_layout(source.local_entries) != _source_layout(fresh.local_entries):
            replan.append(source.source_id)
            deferred.extend(item.entry_key for item in plan.items if item.action is SyncAction.UPDATE_LOCAL)
            continue
        before = {entry.entry_key: entry for entry in source.local_entries}
        fresh_entries = {entry.entry_key: entry for entry in fresh.local_entries}
        remote_sources = RemoteSourceIndex(source.remote_entries)
        items = []
        for item in plan.items:
            if item.action is not SyncAction.UPDATE_LOCAL:
                continue
            original = before[item.entry_key]
            latest = fresh_entries[item.entry_key]
            if (latest.translation, latest.stage, latest.deleted) != (
                original.translation,
                original.stage,
                original.deleted,
            ):
                accepted = remote_sources.get_for_item(item)
                if (latest.translation, latest.stage) != (accepted.translation, accepted.stage):
                    deferred.append(item.entry_key)
                continue
            items.append(item)
        candidate = build_local_candidate(
            fresh.local_entries,
            remote_sources,
            tuple(items),
            attach_remote_refs=False,
            preserve_local_source=True,
        )
        accepted = {item.entry_key for item in items}
        for entry in candidate:
            latest = fresh_entries[entry.entry_key]
            if entry.entry_key in accepted and (entry.translation, entry.stage) != (latest.translation, latest.stage):
                replacements[entry.entry_key] = entry
                updated.append(entry.entry_key)
    return BatchDownloadCandidates(
        tuple(replacements.get(entry.entry_key, entry) for source in current_sources for entry in source.local_entries),
        tuple(updated),
        tuple(deferred),
        tuple(replan),
    )


def _source_layout(entries):
    return tuple(
        (entry.entry_key, entry.original, entry.context, entry.context_order, entry.deleted) for entry in entries
    )


def _validate_sources(sources):
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("download batch contains duplicate source IDs")
    namespaces = [next(iter({entry.entry_key.namespace for entry in source.local_entries}), None) for source in sources]
    present = tuple(namespace for namespace in namespaces if namespace is not None)
    if len(set(present)) != len(present):
        raise ValueError("download batch contains overlapping source identities")
