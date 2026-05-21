"""Story 07: MemoryStore + MemoryRetriever 测试 — CRUD / LRU / 异步写入 / 搜索。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.transbridge.smart_assistant.memory.memory_store import MemoryStore, MemoryEntry


class TestMemoryStore(unittest.TestCase):
    """MemoryStore CRUD + LRU 淘汰 + 异步写入测试。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._dir = Path(self._tmp) / "mem"
        self.store = MemoryStore(self._dir, embedding_mode="disabled", max_entries=10)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 基本 CRUD ──────────────────────────────────────────────

    def test_add_and_get(self):
        entry = MemoryEntry(type="test", summary="条目1", content="内容1")
        mid = self.store.add(entry)
        retrieved = self.store.get(mid)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.summary, "条目1")
        self.assertEqual(retrieved.type, "test")

    def test_add_generates_id(self):
        entry = MemoryEntry(memory_id="", type="test", summary="test")
        mid = self.store.add(entry)
        self.assertTrue(len(mid) > 0)
        self.assertIsNotNone(self.store.get(mid))

    def test_delete(self):
        entry = MemoryEntry(type="test", summary="待删除")
        mid = self.store.add(entry)
        self.assertTrue(self.store.delete(mid))
        self.assertIsNone(self.store.get(mid))

    def test_delete_nonexistent(self):
        self.assertFalse(self.store.delete("nonexistent_id"))

    def test_count(self):
        for i in range(5):
            self.store.add(MemoryEntry(type="test", summary=f"e{i}"))
        self.assertEqual(self.store.count, 5)

    # ── LRU 淘汰 ───────────────────────────────────────────────

    def test_lru_eviction(self):
        for i in range(15):
            self.store.add(MemoryEntry(type="test", summary=f"e{i}"))
        self.assertLessEqual(self.store.count, 10)

    def test_lru_evicts_oldest(self):
        entries = []
        for i in range(12):
            e = MemoryEntry(type="test", summary=f"e{i}")
            mid = self.store.add(e)
            entries.append(mid)
        # e0 and e1 应该被淘汰
        self.assertIsNone(self.store.get(entries[0]))
        self.assertIsNone(self.store.get(entries[1]))
        # e11 应该存在
        self.assertIsNotNone(self.store.get(entries[11]))

    def test_lru_recently_accessed_kept(self):
        entries = []
        for i in range(10):  # 填满但不触发淘汰
            e = MemoryEntry(type="test", summary=f"e{i}")
            mid = self.store.add(e)
            entries.append(mid)
        # 重新访问 e0 使其变成最近使用
        self.store.get(entries[0])
        # 再加 2 个触发淘汰：e1 和 e2 被淘汰，e0 保留
        self.store.add(MemoryEntry(type="test", summary="extra1"))
        self.store.add(MemoryEntry(type="test", summary="extra2"))
        self.assertIsNotNone(self.store.get(entries[0]), "最近访问的 e0 应被保留")

    # ── 搜索 ────────────────────────────────────────────────────

    def test_list_by_type(self):
        self.store.add(MemoryEntry(type="correction", summary="修正1"))
        self.store.add(MemoryEntry(type="correction", summary="修正2"))
        self.store.add(MemoryEntry(type="preference", summary="偏好1"))
        corrections = self.store.list_by_type("correction")
        self.assertEqual(len(corrections), 2)

    # ── 异步写入 ────────────────────────────────────────────────

    def test_async_write_flushes_on_close(self):
        """M9: 关闭时刷盘，确保数据持久化。"""
        self.store.add(MemoryEntry(type="test", summary="持久化测试", content="重要数据"))
        self.store.close()
        # 重新加载
        store2 = MemoryStore(self._dir, embedding_mode="disabled", max_entries=10)
        self.assertGreaterEqual(store2.count, 1)
        store2.close()


if __name__ == "__main__":
    unittest.main()
