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
    from src.transbridge.converter.translation_entry import TranslationEntry

from src.transbridge.converter.context_categories import (
    AUTO_TERM_CONTEXTS,
    ROUND1_CATEGORIES,
    ROUND2_PREFIXES,
    ROUND3_CONTEXTS,
)


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
        plan = BatchPlan()

        # 建立 context 到 round1 类别的快查表
        ctx_to_category: dict[str, str] = {}
        for category, contexts in ROUND1_CATEGORIES.items():
            for ctx in contexts:
                ctx_to_category[ctx] = category

        round1_buckets: dict[str, list["TranslationEntry"]] = {cat: [] for cat in ROUND1_CATEGORIES}
        round2_buckets: dict[str, list["TranslationEntry"]] = {}
        round3_entries: list["TranslationEntry"] = []

        for entry in entries:
            ctx = entry.context or ""
            base_ctx = ctx.split("|")[0] if "|" in ctx else ctx
            rec_type = base_ctx.split(":")[0]

            if base_ctx in ctx_to_category:
                round1_buckets[ctx_to_category[base_ctx]].append(entry)
            elif rec_type in ROUND2_PREFIXES:
                quest_formid = self._get_quest_formid(entry)
                round2_buckets.setdefault(quest_formid, []).append(entry)
            elif base_ctx in ROUND3_CONTEXTS:
                round3_entries.append(entry)
            # 其他未分类的跳过（可视需求放入 round3）

        # 计算自适应字符限制（用于确保足够的批次数以提高并发效率）
        adaptive_char_limit = self._compute_adaptive_char_limit(entries, max_workers)

        # 第一轮：按类别分批
        for category, bucket in round1_buckets.items():
            if bucket:
                for sub_batch in self._split_by_tokens(bucket, adaptive_char_limit):
                    plan.round1.append(Batch(entries=sub_batch, batch_type=category))

        # 第二轮：按 quest_formid 分批
        for quest_formid, bucket in round2_buckets.items():
            if bucket:
                # 对每quest组也应用自适应限制
                effective_limit = self._compute_adaptive_char_limit(bucket, max_workers)
                for sub_batch in self._split_by_tokens(bucket, effective_limit):
                    plan.round2.append(Batch(
                        entries=sub_batch,
                        batch_type="对话",
                        quest_formid=quest_formid,
                    ))

        # 第三轮：统一分批
        for sub_batch in self._split_by_tokens(round3_entries, adaptive_char_limit):
            plan.round3.append(Batch(entries=sub_batch, batch_type="长文本"))

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
        total_chars = sum(len(e.original or "") + len(e.id or "") for e in entries)

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
            entry_chars = len(entry.original or "") + len(entry.id or "")
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
