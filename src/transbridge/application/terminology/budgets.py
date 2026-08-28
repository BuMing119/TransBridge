"""Machine-readable FR5.16.33--FR5.16.40 release budgets.

The values in this module are product requirements, not benchmark baselines.
They must not be adjusted in response to one machine's measurements.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class BudgetLevel(StrEnum):
    SHALL = "shall"
    SHOULD = "should"


class BudgetComparator(StrEnum):
    AT_MOST = "at-most"
    AT_LEAST = "at-least"
    EQUALS = "equals"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class TerminologyBudget:
    requirement_id: str
    metric: str
    comparator: BudgetComparator
    limit: int | float | bool | str
    unit: str
    level: BudgetLevel = BudgetLevel.SHALL
    profile: str = "all"
    exclusions: tuple[str, ...] = ()

    @property
    def check_id(self) -> str:
        return f"{self.requirement_id}:{self.profile}:{self.metric}"


_GIB = 1024**3
_MIB = 1024**2

FR516_BUDGETS: Final[tuple[TerminologyBudget, ...]] = (
    TerminologyBudget("FR5.16.33", "windows-version", BudgetComparator.EQUALS, "Windows 11", "platform"),
    TerminologyBudget("FR5.16.33", "physical-cpu-cores", BudgetComparator.AT_LEAST, 4, "cores"),
    TerminologyBudget("FR5.16.33", "memory", BudgetComparator.AT_LEAST, 16 * _GIB, "bytes"),
    TerminologyBudget("FR5.16.33", "disk-media", BudgetComparator.REQUIRED, "SSD", "class"),
    TerminologyBudget("FR5.16.33", "source-count", BudgetComparator.AT_MOST, 50, "count", profile="regular"),
    TerminologyBudget("FR5.16.33", "evidence-count", BudgetComparator.AT_MOST, 250_000, "count", profile="regular"),
    TerminologyBudget("FR5.16.33", "terminology-count", BudgetComparator.AT_MOST, 50_000, "count", profile="regular"),
    TerminologyBudget("FR5.16.33", "conflict-count", BudgetComparator.AT_MOST, 5_000, "count", profile="regular"),
    TerminologyBudget("FR5.16.33", "history-count", BudgetComparator.AT_MOST, 10, "count", profile="regular"),
    TerminologyBudget("FR5.16.33", "source-count", BudgetComparator.AT_MOST, 200, "count", profile="stress"),
    TerminologyBudget("FR5.16.33", "evidence-count", BudgetComparator.AT_MOST, 1_000_000, "count", profile="stress"),
    TerminologyBudget("FR5.16.33", "terminology-count", BudgetComparator.AT_MOST, 200_000, "count", profile="stress"),
    TerminologyBudget("FR5.16.33", "conflict-count", BudgetComparator.AT_MOST, 20_000, "count", profile="stress"),
    TerminologyBudget("FR5.16.33", "history-count", BudgetComparator.AT_MOST, 50, "count", profile="stress"),
    TerminologyBudget("FR5.16.34", "visible-feedback", BudgetComparator.AT_MOST, 0.5, "seconds"),
    TerminologyBudget("FR5.16.34", "progress-heartbeat", BudgetComparator.AT_MOST, 2.0, "seconds"),
    TerminologyBudget("FR5.16.34", "main-thread-block", BudgetComparator.AT_MOST, 0.2, "seconds"),
    TerminologyBudget(
        "FR5.16.35",
        "local-build",
        BudgetComparator.AT_MOST,
        90.0,
        "seconds",
        profile="regular",
        exclusions=("external-llm-wait", "external-io-wait"),
    ),
    TerminologyBudget(
        "FR5.16.35",
        "local-build",
        BudgetComparator.AT_MOST,
        480.0,
        "seconds",
        profile="stress",
        exclusions=("external-llm-wait", "external-io-wait"),
    ),
    TerminologyBudget(
        "FR5.16.36", "peak-additional-memory", BudgetComparator.AT_MOST, 1.0 * _GIB, "bytes", profile="regular"
    ),
    TerminologyBudget(
        "FR5.16.36", "peak-additional-memory", BudgetComparator.AT_MOST, 2.5 * _GIB, "bytes", profile="stress"
    ),
    TerminologyBudget("FR5.16.36", "stable-growth-after-five-runs", BudgetComparator.AT_MOST, 100 * _MIB, "bytes"),
    TerminologyBudget("FR5.16.37", "cancel-visible-feedback", BudgetComparator.AT_MOST, 0.5, "seconds"),
    TerminologyBudget("FR5.16.37", "cancel-terminal", BudgetComparator.AT_MOST, 3.0, "seconds"),
    TerminologyBudget("FR5.16.38", "exact-reuse", BudgetComparator.AT_MOST, 10.0, "seconds"),
    TerminologyBudget(
        "FR5.16.38", "incremental-changed-evidence", BudgetComparator.AT_MOST, 10.0, "percent", profile="regular"
    ),
    TerminologyBudget(
        "FR5.16.38", "incremental-vs-full", BudgetComparator.AT_MOST, 30.0, "percent", BudgetLevel.SHOULD, "regular"
    ),
    TerminologyBudget("FR5.16.38", "incremental-digest-parity", BudgetComparator.REQUIRED, True, "boolean"),
    TerminologyBudget("FR5.16.39", "query-first-page", BudgetComparator.AT_MOST, 0.5, "seconds", profile="regular"),
    TerminologyBudget("FR5.16.39", "history-open", BudgetComparator.AT_MOST, 2.0, "seconds", profile="regular"),
    TerminologyBudget("FR5.16.39", "compare-summary", BudgetComparator.AT_MOST, 5.0, "seconds", profile="regular"),
    TerminologyBudget("FR5.16.39", "quality-report", BudgetComparator.AT_MOST, 60.0, "seconds", profile="regular"),
    TerminologyBudget("FR5.16.39", "changelog", BudgetComparator.AT_MOST, 30.0, "seconds", profile="regular"),
    TerminologyBudget("FR5.16.39", "export-truncation", BudgetComparator.EQUALS, False, "boolean"),
    TerminologyBudget("FR5.16.40", "llm-wait-separated", BudgetComparator.REQUIRED, True, "boolean"),
    TerminologyBudget("FR5.16.40", "llm-unbounded-retry", BudgetComparator.EQUALS, False, "boolean"),
    TerminologyBudget("FR5.16.40", "deterministic-skip-path", BudgetComparator.REQUIRED, True, "boolean"),
)

_grouped = {
    requirement_id: tuple(item for item in FR516_BUDGETS if item.requirement_id == requirement_id)
    for requirement_id in (f"FR5.16.{index}" for index in range(33, 41))
}
FR516_BUDGETS_BY_REQUIREMENT: Final[Mapping[str, tuple[TerminologyBudget, ...]]] = MappingProxyType(_grouped)


__all__ = [
    "BudgetComparator",
    "BudgetLevel",
    "FR516_BUDGETS",
    "FR516_BUDGETS_BY_REQUIREMENT",
    "TerminologyBudget",
]
