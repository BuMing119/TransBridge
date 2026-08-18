"""
批次规划器。

将 TranslationEntryCollection 按翻译轮次和类型分批。
- 第一轮：固定类别（人名/地名/书名/物品/法术技能/任务名）
- 第二轮：对话类（INFO/DIAL），按 quest_formid 分组
- 第三轮：其余（书籍内容/任务日志等长文本）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transbridge.converter.translation_entry import TranslationEntry


@dataclass
class Batch:
    entries: list["TranslationEntry"]
    batch_type: str            # "人名" | "地名" | "对话" | ...
    quest_formid: str = ""     # 仅对话批次有值


@dataclass
class BatchPlan:
    round1: list[Batch] = field(default_factory=list)
    round2: list[Batch] = field(default_factory=list)
    round3: list[Batch] = field(default_factory=list)

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
    def __init__(self, max_tokens_per_batch: int = 2000):
        self._max_tokens = max_tokens_per_batch

    def plan(self, entries: list["TranslationEntry"], max_workers: int = 0) -> BatchPlan:
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
        char_limit = self._compute_adaptive_char_limit(entries, max_workers)
        context_plan = ContextPlanner(char_limit).plan(planning_entries, action_plan)
        by_key = {entry.identity: entry for entry in entries}
        plan = BatchPlan()
        for context_batch in context_plan.batches:
            batch = Batch(
                entries=[by_key[key] for key in context_batch.keys],
                batch_type=context_batch.category,
                quest_formid=context_batch.quest_id,
            )
            if context_batch.round_number == 1:
                plan.round1.append(batch)
            elif context_batch.round_number == 2:
                plan.round2.append(batch)
            else:
                plan.round3.append(batch)
        return plan

    def _compute_adaptive_char_limit(self, entries: list, max_workers: int) -> int:
        """计算自适应字符限制，确保批次数 >= max_workers * 2。

        当总条目数较少时，缩小批次大小以增加批次数，提高并发效率。
        限制范围：[600, self._max_tokens * 3]
        """
        entry_count = len(entries)
        if max_workers <= 0 or entry_count <= 0:
            return self._max_tokens * 3

        # 目标：批次数 >= max_workers * 2
        target_batches = max_workers * 2

        # 如果条目数本身就很少，无需调整
        if entry_count <= target_batches:
            return self._max_tokens * 3

        # 计算实际总字符数
        total_chars = sum(len(e.original or "") + len(e.key or "") for e in entries)

        # 目标：每批字符数 = 总字符数 / 目标批次数
        # 留10%余量确保达到目标批次数
        adaptive_limit = int((total_chars / target_batches) * 0.9)

        # 限制在合理范围 [600, _max_tokens * 3]
        # 最小600字符约等于10-15条短文本，既保证并发又不会创建过多小批次
        max_char_limit = self._max_tokens * 3
        effective_limit = max(600, min(max_char_limit, adaptive_limit))

        return effective_limit

    def _split_by_tokens(self, entries: list["TranslationEntry"], char_limit: int | None = None) -> list[list["TranslationEntry"]]:
        """简单按字符数估算 token 数，超过阈值则切分。"""
        if not entries:
            return []
        if char_limit is None:
            # 约 1 token ≈ 4 chars（英文）；留余量乘 1.5 安全系数
            char_limit = self._max_tokens * 3

        batches: list[list["TranslationEntry"]] = []
        current: list["TranslationEntry"] = []
        current_chars = 0

        for entry in entries:
            entry_chars = len(entry.original or "") + len(entry.key or "")
            if current and current_chars + entry_chars > char_limit:
                batches.append(current)
                current = []
                current_chars = 0
            current.append(entry)
            current_chars += entry_chars

        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _get_quest_formid(entry: "TranslationEntry") -> str:
        """从 entry.context 解析 quest_formid（如 INFO:NAM1|00012345 → 00012345）。"""
        ctx = entry.context or ""
        if "|" in ctx:
            return ctx.split("|", 1)[1]
        return ""
