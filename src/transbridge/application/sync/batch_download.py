"""Plan source-isolated downloads from fixed local and remote input."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.paratranz_context_order import context_orders
from transbridge.converter.translation_entry import TranslationEntry

from .download_review import DownloadDecision
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


def _validate_sources(sources):
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("download batch contains duplicate source IDs")
    namespaces = [next(iter({entry.entry_key.namespace for entry in source.local_entries}), None) for source in sources]
    present = tuple(namespace for namespace in namespaces if namespace is not None)
    if len(set(present)) != len(present):
        raise ValueError("download batch contains overlapping source identities")
