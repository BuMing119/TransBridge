"""
批次规划器。

将 TranslationEntryCollection 按翻译轮次和类型分批。
- 第一轮：固定类别（人名/地名/书名/物品/法术技能/任务名）
- 第二轮：对话类（INFO/DIAL），按 quest_formid 分组
- 第三轮：其余（书籍内容/任务日志等长文本）
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import TYPE_CHECKING

from transbridge.application.translation.token_batching import OversizedContentItem, StableContentBatcher
from transbridge.infra.token_counting import TiktokenContentTokenCounter

if TYPE_CHECKING:
    from transbridge.application.translation.token_batching import ContentTokenCounter
    from transbridge.converter.translation_entry import TranslationEntry


@dataclass
class Batch:
    entries: list[TranslationEntry]
    batch_type: str  # "人名" | "地名" | "对话" | ...
    quest_formid: str = ""  # 仅对话批次有值
    content_tokens: int = 0
    content_tokens_estimated: bool = False
    fingerprint: str = ""


@dataclass
class BatchPlan:
    round1: list[Batch] = field(default_factory=list)
    round2: list[Batch] = field(default_factory=list)
    round3: list[Batch] = field(default_factory=list)
    oversized: list[OversizedContentItem] = field(default_factory=list)

    def all_batches(self) -> list[Batch]:
        return self.round1 + self.round2 + self.round3

    def total_entries(self) -> int:
        return sum(len(b.entries) for b in self.all_batches())

    def round2_by_quest(self) -> dict[str, list[Batch]]:
        """返回 {quest_formid: [Batch, ...]}，供第二轮按任务串行、任务间并发使用。"""
        groups: dict[str, list[Batch]] = {}
        for batch in self.round2:
            groups.setdefault(batch.quest_formid or "", []).append(batch)
        return groups


class BatchPlanner:
    def __init__(
        self,
        max_tokens_per_batch: int = 2000,
        *,
        model: str = "",
        token_counter: ContentTokenCounter | None = None,
    ) -> None:
        if (
            isinstance(max_tokens_per_batch, bool)
            or not isinstance(max_tokens_per_batch, int)
            or max_tokens_per_batch <= 0
        ):
            raise ValueError("max_tokens_per_batch must be a positive integer")
        self._max_tokens = max_tokens_per_batch
        self._counter = token_counter or TiktokenContentTokenCounter(model)

    def plan(self, entries: list[TranslationEntry], max_workers: int | None = None) -> BatchPlan:
        # Compatibility only: concurrency controls request scheduling, never batch boundaries.
        del max_workers
        from transbridge.application.translation import (
            ActionPlanner,
            ActionRuleSpec,
            ContextPlanner,
            PlanningEntry,
            TranslationAction,
        )

        planning_entries = [
            PlanningEntry(
                entry.identity,
                entry.stage,
                entry.original,
                entry.translation,
                entry.context or "",
            )
            for entry in entries
        ]
        action_plan = ActionPlanner().plan(
            planning_entries,
            [ActionRuleSpec("legacy-batch-planner", 0, TranslationAction.TRANSLATE)],
        )
        # ContextPlanner remains the canonical Round/category/quest classifier.
        # A practically unbounded char limit makes it return one ordered group;
        # business-content Token batching is then the sole split authority here.
        context_plan = ContextPlanner(sys.maxsize).plan(planning_entries, action_plan)
        by_key = {entry.identity: entry for entry in entries}
        plan = BatchPlan()
        for context_batch in context_plan.batches:
            grouped_entries = [by_key[key] for key in context_batch.keys]
            token_plan = StableContentBatcher(self._counter, self._max_tokens).plan(
                grouped_entries,
                key=lambda entry: entry.identity,
                content=lambda entry: entry.original or "",
            )
            plan.oversized.extend(token_plan.oversized)
            for content_batch in token_plan.batches:
                batch = Batch(
                    entries=list(content_batch.items),
                    batch_type=context_batch.category,
                    quest_formid=context_batch.quest_id,
                    content_tokens=content_batch.content_tokens,
                    content_tokens_estimated=content_batch.is_estimate,
                    fingerprint=content_batch.fingerprint,
                )
                if context_batch.round_number == 1:
                    plan.round1.append(batch)
                elif context_batch.round_number == 2:
                    plan.round2.append(batch)
                else:
                    plan.round3.append(batch)
        return plan
