"""翻译记忆（Translation Memory）单元测试。

覆盖：独立快照、add 双索引、query 多词典组合查询（同名 mod 优先 + 冲突仲裁）、
存为词典、套用到集合、scope 校验、merge、主键 scope 解耦。
"""

from __future__ import annotations

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.translation_memory import (
    QueryContext,
    TranslationMemoryManager,
)
from transbridge.translation_memory.model import (
    Dictionary,
    entry_id,
)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


def test_dictionary_invalid_scope():
    with pytest.raises(ValueError):
        Dictionary(scope="invalid")


def test_dictionary_snapshot_preserves_entries_and_indexes_without_aliasing():
    manager = TranslationMemoryManager()
    manager.add("K|1~NPC_:FULL", "原", "译", mod_file_id="LegacyPatch", scope="project", tags=["a"])
    dictionary = manager.dictionaries["LegacyPatch"]
    (snapshot,) = manager.snapshot_dictionaries()
    assert snapshot == dictionary
    entry = next(iter(snapshot.entries.values()))
    entry.tags.append("b")
    snapshot.key_index["K|1~NPC_:FULL"]["hits"] = 3
    snapshot.text_index["原"]["hits"] = 5
    assert next(iter(dictionary.entries.values())).tags == ["a"]
    assert dictionary.key_index["K|1~NPC_:FULL"]["hits"] == 0
    assert dictionary.text_index["原"]["hits"] == 0


def test_entry_id_excludes_scope():
    """主键不含 scope：同 (mod_file_id, 原文) 切换 scope 不换 ID。"""
    id1 = entry_id("LegacyPatch", "Hello")
    assert id1 == entry_id("LegacyPatch", "Hello")
    # 不同 mod 名 → 不同主键
    assert id1 != entry_id("OtherMod", "Hello")
    # 不同原文 → 不同主键
    assert id1 != entry_id("LegacyPatch", "World")


# ---------------------------------------------------------------------------
# add / query
# ---------------------------------------------------------------------------


def test_add_single_authority_object_and_two_indexes():
    m = TranslationMemoryManager()
    m.add("K1|1~NPC_:FULL", "Hello there", "你好", mod_file_id="LegacyPatch", scope="global")
    d = m.dictionaries["LegacyPatch"]
    # 权威对象只存一份
    assert len(d.entries) == 1
    # 键索引与文本索引都登记，指向同一 entry_id
    eid = d.key_index["K1|1~NPC_:FULL"]["entry_id"]
    assert d.text_index["Hello there"]["entry_id"] == eid
    assert d.entries[eid].translation == "你好"
    assert d.entries[eid].source_mod == "LegacyPatch"


def test_query_key_exact_and_stale():
    m = TranslationMemoryManager()
    m.add("K1|1~NPC_:FULL", "Hello there", "你好", mod_file_id="LegacyPatch", scope="global")
    exact = m.query("K1|1~NPC_:FULL", "Hello there", QueryContext(mod_file_id="LegacyPatch"))
    assert exact.translation == "你好"
    assert exact.match_status == "EXACT"
    assert exact.matched_via == "key"
    assert exact.matched_mod == "LegacyPatch"
    # 原文变化 → STALE
    stale = m.query("K1|1~NPC_:FULL", "Hello everywhere", QueryContext(mod_file_id="LegacyPatch"))
    assert stale.translation == "你好"
    assert stale.match_status == "STALE"


def test_query_text_fallback():
    m = TranslationMemoryManager()
    m.add("", "Find the key", "找到钥匙", mod_file_id="ModA", scope="project")
    # 同名 mod 命中（文本）
    r = m.query("", "Find the key", QueryContext(mod_file_id="ModA"))
    assert r.translation == "找到钥匙"
    assert r.matched_via == "text"
    # 非同名 mod，但 ModA 是 project（其余 project 词典也会兜底，除非没有其它词典）
    # 用空 mod_file_id 查询（走其余 project/global 全查）
    r2 = m.query("", "Find the key", QueryContext(mod_file_id="OtherMod"))
    assert r2.translation == "找到钥匙"  # 兜底命中 project 词典


def test_query_key_priority_over_text():
    """同名 mod 词典内：键命中优先于文本命中。"""
    m = TranslationMemoryManager()
    m.add("K1|1~NPC_:FULL", "Hello", "键译文", mod_file_id="ModA", scope="project")
    # 同名 mod 键命中优先
    r = m.query("K1|1~NPC_:FULL", "Hello", QueryContext(mod_file_id="ModA"))
    assert r.translation == "键译文"
    assert r.matched_via == "key"


def test_query_multi_dict_conflict_arbitration():
    """多词典命中同一原文、译文不同 → 冲突收集 + project 优先于 global。"""
    m = TranslationMemoryManager()
    # global 词典与 project 词典对同一原文给出不同译文
    m.add("", "Hello", "全局译文", mod_file_id="ModG", scope="global")
    m.add("", "Hello", "项目译文", mod_file_id="ModP", scope="project")
    # 查询目标 mod 是 ModX（同名词典不存在），其余 project(ModP) 优先于 global(ModG)
    r = m.query("", "Hello", QueryContext(mod_file_id="ModX"))
    assert r.translation == "项目译文"
    assert r.conflicts  # 存在冲突（全局译文与项目译文不同）
    confs = {c["translation"] for c in r.conflicts}
    assert "全局译文" in confs


def test_query_multi_dict_no_conflict_when_same_translation():
    """多词典命中同一原文、译文相同 → 无冲突。"""
    m = TranslationMemoryManager()
    m.add("", "Hello", "你好", mod_file_id="ModG", scope="global")
    m.add("", "Hello", "你好", mod_file_id="ModP", scope="project")
    r = m.query("", "Hello", QueryContext(mod_file_id="ModX"))
    assert r.translation == "你好"
    assert not r.conflicts


def test_empty_translation_not_added():
    m = TranslationMemoryManager()
    m.add("K1", "Hello", "", mod_file_id="LegacyPatch", scope="global")
    assert "LegacyPatch" not in m.dictionaries


def test_requires_mod_file_id():
    m = TranslationMemoryManager()
    with pytest.raises(ValueError):
        m.add("K1", "Hello", "你好", mod_file_id="", scope="project")


# ---------------------------------------------------------------------------
# 存为词典 / 套用
# ---------------------------------------------------------------------------


def _make_collection():
    entries = [
        TranslationEntry(
            id="E1:0001|1~NPC_:FULL",
            key="E1:0001|1~NPC_:FULL",
            original="Hello",
            translation="你好",
            stage=1,
            context="NPC_:FULL",
        ),
        TranslationEntry(
            id="E2:0002|1~NPC_:FULL",
            key="E2:0002|1~NPC_:FULL",
            original="World",
            translation="世界",
            stage=1,
            context="NPC_:FULL",
        ),
        TranslationEntry(
            id="E3:0003|1~NPC_:FULL",
            key="E3:0003|1~NPC_:FULL",
            original="Empty translation",
            translation="",
            stage=0,
            context="NPC_:FULL",
        ),
        TranslationEntry(
            id="E4:0004|1~NPC_:FULL",
            key="E4:0004|1~NPC_:FULL",
            original="Locked",
            translation="锁定",
            stage=9,
            context="NPC_:FULL",
        ),
    ]
    return TranslationEntryCollection(entries)


def test_save_from_collection_skips_empty_and_locked():
    m = TranslationMemoryManager()
    c = _make_collection()
    added = m.save_from_collection(c, mod_file_id="LegacyPatch", scope="project")
    # E1、E2 写入；E3 空译文跳过；E4 stage==9 锁定跳过
    assert added == 2
    d = m.dictionaries["LegacyPatch"]
    assert len(d.entries) == 2


def test_save_from_collection_persists_source_mod():
    m = TranslationMemoryManager()
    c = _make_collection()
    m.save_from_collection(c, mod_file_id="LegacyPatch", scope="project")
    d = m.dictionaries["LegacyPatch"]
    for e in d.entries.values():
        assert e.source_mod == "LegacyPatch"


def test_apply_to_collection_fills_empty_only():
    m = TranslationMemoryManager()
    m.add("", "Hello", "你好", mod_file_id="ModG", scope="global")
    entries = [
        TranslationEntry(
            id="X:1|1~NPC_:FULL", key="X:1|1~NPC_:FULL", original="Hello", translation="", stage=0, context="NPC_:FULL"
        ),
        TranslationEntry(
            id="X:2|1~NPC_:FULL",
            key="X:2|1~NPC_:FULL",
            original="Hello",
            translation="已有",
            stage=1,
            context="NPC_:FULL",
        ),
    ]
    c = TranslationEntryCollection(entries)
    r = m.apply_to_collection(c, QueryContext(mod_file_id=""))
    assert r.text_hits == 1
    assert r.applied == 1
    vals = [e.translation for e in c]
    assert vals.count("你好") == 1
    assert vals.count("已有") == 1


def test_apply_excludes_locked_stage():
    m = TranslationMemoryManager()
    m.add("", "Locked", "译文", mod_file_id="ModG", scope="global")
    entry = TranslationEntry(
        id="L:1|1~NPC_:FULL", key="L:1|1~NPC_:FULL", original="Locked", translation="", stage=9, context="NPC_:FULL"
    )
    c = TranslationEntryCollection([entry])
    r = m.apply_to_collection(c, QueryContext(mod_file_id=""))
    assert r.applied == 0
    assert entry.translation == ""  # 锁定条目不被套用


# ---------------------------------------------------------------------------
# 命中计数 / merge / scope
# ---------------------------------------------------------------------------


def test_hit_count_increments_once_per_index():
    m = TranslationMemoryManager()
    m.add("K1|1~NPC_:FULL", "Hello", "你好", mod_file_id="LegacyPatch", scope="global")
    m.query("K1|1~NPC_:FULL", "Hello", QueryContext(mod_file_id="LegacyPatch"))
    m.query("", "Hello", QueryContext(mod_file_id="LegacyPatch"))
    d = m.dictionaries["LegacyPatch"]
    assert d.key_index["K1|1~NPC_:FULL"]["hits"] == 1
    assert d.text_index["Hello"]["hits"] == 1


def test_merge_counts_added():
    m1 = TranslationMemoryManager()
    m1.add("K1", "Hello", "你好", mod_file_id="ModA", scope="global")
    m2 = TranslationMemoryManager()
    m2.add("K2", "World", "世界", mod_file_id="ModB", scope="global")
    added = m1.merge(m2)
    assert added == 1


def test_normalize_unifies_newlines():
    m = TranslationMemoryManager()
    m.add("", "Line1\r\nLine2", "第一行\n第二行", mod_file_id="ModA", scope="global")
    r = m.query("", "Line1\nLine2", QueryContext(mod_file_id="ModA"))
    assert r.translation == "第一行\n第二行"


def test_set_scope_switch():
    m = TranslationMemoryManager()
    m.add("K1", "Hello", "你好", mod_file_id="ModA", scope="global")
    assert m.dictionaries["ModA"].scope == "global"
    m.set_scope("ModA", "project")
    assert m.dictionaries["ModA"].scope == "project"
    with pytest.raises(ValueError):
        m.set_scope("ModA", "invalid")
