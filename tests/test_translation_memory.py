"""翻译记忆（Translation Memory）单元测试。

覆盖：数据模型往返、add 双索引、query 多词典组合查询（同名 mod 优先 + 冲突仲裁）、
存为词典、套用到集合、持久化往返（.tbdict）、scope 校验、merge、主键 scope 解耦。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.translation_memory import (
    QueryContext,
    TranslationMemoryManager,
)
from transbridge.translation_memory.model import (
    Dictionary,
    DictionaryEntry,
    entry_id,
)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


def test_dictionary_entry_roundtrip():
    e = DictionaryEntry(
        translation="你好",
        original="Hello",
        source_mod="LegacyPatch",
        form_id_with_plugin="0001A2B3|LegacyPatch.esp",
        imported_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        tags=["a", "b"],
    )
    e2 = DictionaryEntry.from_dict(e.to_dict())
    assert e2 == e
    assert e2.source_mod == "LegacyPatch"
    assert e2.form_id_with_plugin == "0001A2B3|LegacyPatch.esp"
    assert e2.tags == ["a", "b"]


def test_dictionary_invalid_scope():
    with pytest.raises(ValueError):
        Dictionary(scope="invalid")


def test_dictionary_roundtrip_with_indexes():
    d = Dictionary(scope="project", mod_file_id="LegacyPatch")
    d.entries["id1"] = DictionaryEntry(translation="译", original="原")
    d.key_index["K|1~NPC_:FULL"] = {"entry_id": "id1", "hits": 3}
    d.text_index["原"] = {"entry_id": "id1", "hits": 5}
    d2 = Dictionary.from_dict(d.to_dict())
    assert d2.scope == "project"
    assert d2.mod_file_id == "LegacyPatch"
    assert d2.entries["id1"].translation == "译"
    assert d2.key_index["K|1~NPC_:FULL"]["hits"] == 3
    assert d2.text_index["原"]["hits"] == 5
    assert d2.to_dict()["schema_version"] == 2


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
# 持久化 / merge / 导入导出
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tm_tmp_dir: Path):
    m = TranslationMemoryManager()
    m.add("K1|1~NPC_:FULL", "Hello there", "你好", mod_file_id="LegacyPatch", scope="global")
    m.add("", "Find the key", "找到钥匙", mod_file_id="OtherMod", scope="project")
    files = m.save(tm_tmp_dir)
    assert len(files) == 2
    assert all(f.suffix == ".tbdict" for f in files)

    m2 = TranslationMemoryManager()
    n = m2.load(tm_tmp_dir)
    assert n == 2
    r = m2.query("K1|1~NPC_:FULL", "Hello there", QueryContext(mod_file_id="LegacyPatch"))
    assert r.translation == "你好"
    assert r.match_status == "EXACT"
    r2 = m2.query("", "Find the key", QueryContext(mod_file_id="OtherMod"))
    assert r2.translation == "找到钥匙"


def test_hit_count_persists_and_increments_once_per_table():
    m = TranslationMemoryManager()
    m.add("K1|1~NPC_:FULL", "Hello", "你好", mod_file_id="LegacyPatch", scope="global")
    m.query("K1|1~NPC_:FULL", "Hello", QueryContext(mod_file_id="LegacyPatch"))
    m.query("", "Hello", QueryContext(mod_file_id="LegacyPatch"))
    d = m.dictionaries["LegacyPatch"]
    assert d.key_index["K1|1~NPC_:FULL"]["hits"] == 1
    assert d.text_index["Hello"]["hits"] == 1


def test_load_corrupt_file_raises_and_preserves(tm_tmp_dir: Path):
    m = TranslationMemoryManager()
    m.add("K1", "Hello", "你好", mod_file_id="LegacyPatch", scope="global")
    m.save(tm_tmp_dir)
    # 破坏 LegacyPatch.tbdict
    bad = tm_tmp_dir / "LegacyPatch.tbdict"
    bad.write_text("{ not valid json", encoding="utf-8")
    m2 = TranslationMemoryManager()
    with pytest.raises(RuntimeError):
        m2.load(tm_tmp_dir)
    # 现场保留为 .corrupt-*
    assert list(tm_tmp_dir.glob("*.corrupt-*"))


def test_load_duplicate_mod_raises(tm_tmp_dir: Path):
    """同名 .tbdict（mod_file_id 重复）应抛错，不静默覆盖。"""
    m = TranslationMemoryManager()
    m.add("K1", "Hello", "你好", mod_file_id="LegacyPatch", scope="global")
    m.save(tm_tmp_dir)
    # 手动复制出另一个同名文件（不同内容）触发重复检测比较难，改为直接写入第二个文件
    (tm_tmp_dir / "LegacyPatch.tbdict").write_text(
        '{"schema_version": 1, "mod_file_id": "LegacyPatch", "scope": "global", '
        '"entries": {}, "key_index": {}, "text_index": {}}',
        encoding="utf-8",
    )
    # 已有一个 LegacyPatch.tbdict，且目录里还有另一文件——但 load 只 glob 一次，重复在内存层检测
    # 这里验证：同一 mod_file_id 出现两份文件时抛错
    (tm_tmp_dir / "LegacyPatch_copy.tbdict").write_text(
        '{"schema_version": 1, "mod_file_id": "LegacyPatch", "scope": "global", '
        '"entries": {}, "key_index": {}, "text_index": {}}',
        encoding="utf-8",
    )
    m2 = TranslationMemoryManager()
    with pytest.raises(RuntimeError):
        m2.load(tm_tmp_dir)


def test_failed_reload_preserves_previous_snapshot_and_duplicate_files(tmp_path):
    manager = TranslationMemoryManager(base_dir=tmp_path)
    manager.add("key", "Original", "Translation", mod_file_id="mod")
    manager.save()
    manager.load()
    before = manager.snapshot_dictionaries()
    source = tmp_path / "mod.tbdict"
    duplicate = tmp_path / "duplicate.tbdict"
    duplicate.write_bytes(source.read_bytes())
    with pytest.raises(RuntimeError, match="mod_file_id 重复"):
        manager.load()
    assert source.exists() and duplicate.exists()
    assert not list(tmp_path.glob("*.corrupt-*"))
    assert manager.snapshot_dictionaries() == before


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


def test_import_dict(tm_tmp_dir: Path):
    # 源目录：有一本 ModA.tbdict
    src_dir = tm_tmp_dir / "src"
    src_dir.mkdir(exist_ok=True)
    m_src = TranslationMemoryManager(base_dir=src_dir)
    m_src.add("K1", "Hello", "你好", mod_file_id="ModA", scope="global")
    m_src.save()

    # 目标目录：另一个 base_dir
    dst_dir = tm_tmp_dir / "dst"
    dst_dir.mkdir(exist_ok=True)
    src_file = src_dir / "ModA.tbdict"

    m2 = TranslationMemoryManager(base_dir=dst_dir)
    ok = m2.import_dict(src_file)
    assert ok is True
    assert (dst_dir / "ModA.tbdict").exists()

    # 同名再次导入（不覆盖）→ False
    ok2 = m2.import_dict(src_file)
    assert ok2 is False
    # 覆盖 → True
    ok3 = m2.import_dict(src_file, overwrite=True)
    assert ok3 is True
