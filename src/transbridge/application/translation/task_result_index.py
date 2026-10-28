"""Immutable counts and source projections computed alongside a task ledger."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .task_summary import summarize_entries


@dataclass(frozen=True)
class TaskResultIndex:
    failed: frozenset
    ready: frozenset
    applied: frozenset
    pending_preview: frozenset
    summary: object
    sources: object
    applied_review: int


def index_results(entries) -> TaskResultIndex:
    failed, ready, applied, preview = set(), set(), set(), set()
    sources = {}
    review = 0
    for key, row in entries.items():
        sources.setdefault(row.source, {})[key] = row
        if row.status == "failed" and not row.applied:
            failed.add(key)
        if row.applied:
            applied.add(key)
            review += row.stage == 2
        if row.status == "succeeded" and row.decision != "rejected" and not row.applied:
            ready.add(key)
            if row.action == "polish" and row.decision == "pending":
                preview.add(key)
    return TaskResultIndex(
        frozenset(failed),
        frozenset(ready),
        frozenset(applied),
        frozenset(preview),
        summarize_entries(entries),
        MappingProxyType({source: summarize_entries(rows) for source, rows in sources.items()}),
        review,
    )
