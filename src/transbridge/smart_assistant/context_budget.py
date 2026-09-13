"""Offline, explicit joint budgets for assistant context projections."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
import json
from typing import Any

from .context_capacity import FALLBACK_CONTEXT_WINDOW
from .context_estimation import ESTIMATOR_LABEL, estimate_tokens


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

    def describe(self) -> str:
        return (
            f"本地估算 {self.total:,} / 配置窗口 {self.context_window:,} token"
            f"（消息 {self.messages:,}、工具定义 {self.tool_schemas:,}、"
            f"输出预留 {self.output_reserve:,}、协议余量 {self.protocol_margin:,}；"
            f"估算方式 {self.estimator_label}，不是服务端实际用量）"
        )


class ContextBudgetExceeded(ValueError):
    """Required instructions/input cannot fit; callers must block dispatch."""

    code = "CONTEXT_BUDGET_EXCEEDED"

    def __init__(self, usage: ContextUsage) -> None:
        self.usage = usage
        super().__init__(f"{self.code}: {usage.describe()}。请在设置 → AI 服务中核对助手上下文容量或缩小材料。")


@dataclass(frozen=True)
class ContextBudget:
    context_window: int = FALLBACK_CONTEXT_WINDOW
    output_reserve: int = 4096
    protocol_margin: int = 512
    estimator: Callable[[str], int] = field(default=estimate_tokens, repr=False, compare=False)
    estimator_label: str = ESTIMATOR_LABEL

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


def budget_for_config(config, output_reserve=4096, *, context_window=None) -> ContextBudget:
    from .context_capacity import resolve_context_capacity
    from .context_estimation import select_estimator

    capacity = resolve_context_capacity(config, override=context_window)
    estimator, label = select_estimator(str(getattr(config, "model", "") or ""))
    return ContextBudget(capacity.window, output_reserve, estimator=estimator, estimator_label=label)
