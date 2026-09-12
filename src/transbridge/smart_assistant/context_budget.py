"""Offline, explicit joint budgets for assistant context projections."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
import json
import math
from typing import Any


def _offline_estimate(text: str) -> int:
    # Same fallback bound as infra.token_counting, without its translation
    # pipeline import or any encoding download. A ready tokenizer is injectable.
    return math.ceil(len(text.encode("utf-8")) * 1.25)


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Unsupported context value: {type(value).__name__}")


def context_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default, separators=(",", ":"))


@dataclass(frozen=True)
class ContextUsage:
    messages: int
    tool_schemas: int
    output_reserve: int
    protocol_margin: int
    context_window: int
    estimator_label: str

    @property
    def total(self) -> int:
        return self.messages + self.tool_schemas + self.output_reserve + self.protocol_margin

    @property
    def fits(self) -> bool:
        return self.total <= self.context_window


class ContextBudgetExceeded(ValueError):
    """Required instructions/input cannot fit; callers must block dispatch."""

    code = "CONTEXT_BUDGET_EXCEEDED"

    def __init__(self, usage: ContextUsage) -> None:
        self.usage = usage
        super().__init__(
            f"{self.code}: required context needs {usage.total} estimated tokens, "
            f"configured window is {usage.context_window}; reduce materials or increase the configured window."
        )


@dataclass(frozen=True)
class ContextBudget:
    context_window: int = 32768
    output_reserve: int = 4096
    protocol_margin: int = 512
    estimator: Callable[[str], int] = field(default=_offline_estimate, repr=False, compare=False)
    estimator_label: str = "utf8-bytes-v1-conservative"

    def __post_init__(self) -> None:
        for name in ("context_window", "output_reserve", "protocol_margin"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not self.context_window or not self.estimator_label.strip():
            raise ValueError("context_window and estimator_label must be explicit")

    def count(self, value: Any) -> int:
        count = self.estimator(context_json(value))
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Context estimator must return a non-negative integer")
        return count

    def measure(self, messages: Sequence[dict], tools: Sequence[Any] = ()) -> ContextUsage:
        return ContextUsage(
            self.count(list(messages)),
            self.count(list(tools)) if tools else 0,
            self.output_reserve,
            self.protocol_margin,
            self.context_window,
            self.estimator_label,
        )

    def require(self, messages: Sequence[dict], tools: Sequence[Any] = ()) -> ContextUsage:
        usage = self.measure(messages, tools)
        if not usage.fits:
            raise ContextBudgetExceeded(usage)
        return usage
