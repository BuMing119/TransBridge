"""词条键对齐迁移：按 entry.key 将旧集合译文对齐到新集合同名键条目。"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.transbridge.converter.translation_entry import (
    TranslationEntry, STAGE_TRANSLATED, _normalize_text,
)


@dataclass
class MigrationResult:
    inherited: int = 0                 # 键命中且原文未变，直接继承
    needs_review: list = field(default_factory=list)   # 键命中但原文变化（entry.key 列表）
    missed: int = 0                    # 键未命中，保留待翻译

    def to_dict(self) -> dict:
        return {
            "inherited": self.inherited,
            "needs_review": self.needs_review,
            "missed": self.missed,
        }


def migrate(old_collection, new_collection) -> MigrationResult:
    """按 entry.key 将旧集合译文对齐到新集合同名键条目。

    键命中且原文未变 → 继承译文（stage=已翻译）；
    键命中但原文变化 → 标记需复核（不套用）；
    键未命中 → 保留待翻译（count missed）。

    不修改旧集合，仅就地填充 new_collection 的译文。
    """
    if old_collection is None or new_collection is None:
        return MigrationResult()

    # 构建 old_collection 的 key → entry 映射
    old_by_key = {}
    for e in old_collection:
        if e.key:
            old_by_key.setdefault(e.key, e)

    result = MigrationResult()
    for e in new_collection:
        if not e.key or e.key not in old_by_key:
            result.missed += 1
            continue
        old = old_by_key[e.key]
        if not old.translation:
            result.missed += 1
            continue
        if _normalize_text(old.original) == _normalize_text(e.original):
            e.translation = old.translation
            e.stage = STAGE_TRANSLATED
            result.inherited += 1
        else:
            # 原文变化：标记需复核，不套用
            result.needs_review.append(e.key)
    return result