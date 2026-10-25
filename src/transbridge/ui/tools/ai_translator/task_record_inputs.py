"""Small source descriptors and private ledger snapshots for background reports."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace

from .source_execution import SourceOutcome
from .task_scope import SourceTask


@dataclass(frozen=True)
class RecordSource:
    key: str
    label: str
    error: str
    diagnostics: tuple


def source_descriptors(outcomes):
    """Capture O(sources) scalars, without touching mutable task collections."""
    return tuple(RecordSource(item.task.key, item.task.label, item.error, tuple(item.diagnostics)) for item in outcomes)


def ledger_outcomes(sources, entry_results, *, cancelled):
    """Build reports in one worker from rows owned by an immutable ledger generation."""
    grouped = {}
    for key, row in entry_results.entries.items():
        grouped.setdefault(row.source, {})[key] = row
    outcomes = []
    for source in sources:
        subset = copy(entry_results)
        subset.entries = grouped.get(source.key, {})
        outcomes.append(
            SourceOutcome(
                task=SourceTask(source.key, source.label, None, None, (), ()),
                error=source.error,
                record_snapshot=replace(
                    subset.snapshot(source.key, cancelled=cancelled), diagnostics=source.diagnostics
                ),
            )
        )
    return tuple(outcomes)
