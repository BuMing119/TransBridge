"""词条键对齐迁移测试：键匹配继承 + 原文变化检测 + 键未命中。"""

from __future__ import annotations

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.migrator import migrate


def _mk(key, original, translation="", stage=0):
    return TranslationEntry(
        id=key, key=key, original=original, translation=translation,
        stage=stage, context="NPC_:FULL",
    )


def _col(*entries):
    c = TranslationEntryCollection()
    for e in entries:
        c.add(e)
    return c


def test_migrate_inherit_exact():
    """键命中且原文未变 → 继承译文。"""
    old = _col(_mk("K1", "Iron Armor", "铁甲", stage=1))
    new = _col(_mk("K1", "Iron Armor"))
    result = migrate(old, new)
    assert result.inherited == 1
    assert result.missed == 0
    assert result.needs_review == []
    # 新集合译文被填充
    assert list(new)[0].translation == "铁甲"


def test_migrate_original_changed():
    """键命中但原文变化 → 标记需复核，不套用。"""
    old = _col(_mk("K1", "Iron Armor", "铁甲", stage=1))
    new = _col(_mk("K1", "Iron Armor (Tempered)"))
    result = migrate(old, new)
    assert result.inherited == 0
    assert "K1" in result.needs_review
    # 新集合译文未被填充
    assert list(new)[0].translation == ""


def test_migrate_key_miss():
    """键未命中 → missed，保留待翻译。"""
    old = _col(_mk("K1", "Iron Armor", "铁甲", stage=1))
    new = _col(_mk("K2", "Steel Sword"))
    result = migrate(old, new)
    assert result.missed == 1
    assert result.inherited == 0


def test_migrate_empty_old():
    """旧集合为空 → 全部 missed。"""
    old = _col()
    new = _col(_mk("K1", "Iron Armor"))
    result = migrate(old, new)
    assert result.missed == 1


def test_migrate_normalize_whitespace():
    """原文规范化：换行/空白差异不算变化。"""
    old = _col(_mk("K1", "Hello\r\nWorld", "你好世界", stage=1))
    new = _col(_mk("K1", "Hello\nWorld"))
    result = migrate(old, new)
    assert result.inherited == 1