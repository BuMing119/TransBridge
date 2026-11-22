# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Stage summary and source-wide counts used by the workbench and its read tools."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace

from transbridge.application.workbench_filters import FilterState, filtered_rows
from transbridge.converter.context_categories import context_category
from transbridge.converter.translation_entry import (
    STAGE_CHECKED,
    STAGE_HIDDEN,
    STAGE_LABELS,
    STAGE_LOCKED,
    STAGE_QUESTIONABLE,
    STAGE_REVIEWED,
    STAGE_TRANSLATED,
    STAGE_UNTRANSLATED,
    TranslationEntry,
)


@dataclass(frozen=True, slots=True)
class StatisticsMetric:
    key: str
    label: str
    value: int
    description: str


_COMPLETED_STAGE_SEQUENCE = (STAGE_TRANSLATED, STAGE_CHECKED, STAGE_REVIEWED, STAGE_LOCKED)
_COMPLETED_STAGES = frozenset(_COMPLETED_STAGE_SEQUENCE)
_SUMMARY_STAGE_FILTERS = {
    "untranslated": frozenset((STAGE_UNTRANSLATED,)),
    "review": frozenset((STAGE_QUESTIONABLE,)),
    "completed": _COMPLETED_STAGES,
    "hidden": frozenset((STAGE_HIDDEN,)),
    "total": frozenset(),
}


@dataclass(frozen=True, slots=True)
class StatisticsSummary:
    total: int
    untranslated: int
    needs_review: int
    completed: int
    hidden: int = 0

    def __post_init__(self) -> None:
        if min(self.total, self.untranslated, self.needs_review, self.completed, self.hidden) < 0:
            raise ValueError("Workbench statistics cannot be negative")
        if self.untranslated + self.needs_review + self.completed + self.hidden != self.total:
            raise ValueError("Workbench statistics must partition the collection")

    @classmethod
    def from_entries(cls, entries: Sequence[TranslationEntry]) -> StatisticsSummary:
        return cls.from_stage_counts(Counter(entry.stage for entry in entries))

    @classmethod
    def from_stage_counts(cls, stages: Counter[int]) -> StatisticsSummary:
        total = stages.total()
        completed = sum(stages[stage] for stage in _COMPLETED_STAGES)
        review = stages[STAGE_QUESTIONABLE]
        hidden = stages[STAGE_HIDDEN]
        # The workbench also groups unrecognized external stages with untranslated entries.
        return cls(total, total - completed - review - hidden, review, completed, hidden)

    @property
    def translation_rate(self) -> float:
        return round(100 * self.completed / self.total, 1) if self.total else 0.0

    def metrics(self) -> tuple[StatisticsMetric, ...]:
        """Return status counts using the canonical translation-stage vocabulary."""

        completed_labels = "、".join(STAGE_LABELS[stage] for stage in _COMPLETED_STAGE_SEQUENCE)
        return (
            StatisticsMetric("total", "全部", self.total, "全部翻译状态"),
            StatisticsMetric(
                "untranslated",
                STAGE_LABELS[STAGE_UNTRANSLATED],
                self.untranslated,
                f"翻译状态：{STAGE_LABELS[STAGE_UNTRANSLATED]}",
            ),
            StatisticsMetric(
                "review",
                STAGE_LABELS[STAGE_QUESTIONABLE],
                self.needs_review,
                f"翻译状态：{STAGE_LABELS[STAGE_QUESTIONABLE]}",
            ),
            StatisticsMetric(
                "completed",
                STAGE_LABELS[STAGE_TRANSLATED],
                self.completed,
                f"包括翻译状态：{completed_labels}",
            ),
        )

    def filter_state(self, key: str, current: FilterState) -> FilterState:
        try:
            stages = _SUMMARY_STAGE_FILTERS[key]
        except KeyError:
            raise KeyError(key) from None
        return replace(current, stages=stages)

    @staticmethod
    def active_filter(stages: frozenset[int]) -> str | None:
        return next((key for key, value in _SUMMARY_STAGE_FILTERS.items() if key != "hidden" and value == stages), None)


@dataclass(frozen=True, slots=True)
class ScopeStatistics:
    categories: Counter[str]
    stages: Counter[int]
    labels: Counter[str]
    labeled: int
    summary: StatisticsSummary


def _scope_statistics(facts):
    categories, stages, labels = Counter(), Counter(), Counter()
    labeled = 0
    for context, stage, membership, count in facts:
        categories[context_category(context)] += count
        stages[stage] += count
        if membership:
            labeled += count
            for label in membership:
                labels[label] += count
    return ScopeStatistics(categories, stages, labels, labeled, StatisticsSummary.from_stage_counts(stages))


def scope_statistics(collection, entry_labels=None, *, keys=None, labels_for_entry=None):
    """Read the complete source, using the revision-bound aggregate on paged projects."""
    read_facts = getattr(collection, "statistics", None)
    if callable(read_facts):
        facts = read_facts() if keys is None else read_facts(keys=keys)
        return _scope_statistics(
            (context, stage, membership, count) for (context, stage, _translated, membership), count in facts.items()
        )
    entries = () if collection is None else collection
    if labels_for_entry is not None:
        if keys is not None:
            entries = (entry for key in keys if (entry := collection.get(key)) is not None)
        return _scope_statistics((entry.context or "", entry.stage, labels_for_entry(entry), 1) for entry in entries)
    rows = filtered_rows(entries, FilterState(), entry_labels)
    return _scope_statistics(
        (entry.context or "", entry.stage, labels, 1)
        for entry, _, labels in rows
        if keys is None or entry.identity in keys
    )
