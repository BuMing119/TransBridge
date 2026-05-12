"""长期记忆存储：FAISS 向量索引 + JSON 元数据双存储。

M9: add() 异步写入 — 仅做内存操作后立即返回，由 MemoryWriterThread 后台刷盘。
"""

import json
import logging
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QThread

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    memory_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: str = "conversation"  # preference / term_decision / correction / conversation
    summary: str = ""
    content: str = ""
    source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id, "type": self.type,
            "summary": self.summary, "content": self.content,
            "source": self.source, "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class MemoryWriterThread(QThread):
    """M9: 后台写入线程，批量刷写记忆数据到磁盘。"""

    def __init__(self, storage_dir: Path, metadata_path: Path, index_path: Path,
                 get_metadata_cb, get_vector_store_cb):
        super().__init__()
        self._queue: deque = deque()
        self._cv = threading.Condition()
        self._storage_dir = storage_dir
        self._metadata_path = metadata_path
        self._index_path = index_path
        self._get_metadata = get_metadata_cb
        self._get_vector_store = get_vector_store_cb
        self._running = True

    def enqueue(self) -> None:
        """通知 writer 有数据待刷盘。"""
        with self._cv:
            self._cv.notify()

    def run(self) -> None:
        while self._running:
            with self._cv:
                self._cv.wait(timeout=0.5)
            if not self._running:
                break
            try:
                self._flush()
            except Exception as exc:
                logger.warning("MemoryWriter 刷盘失败: %s", exc)

    def _flush(self) -> None:
        """批量将元数据和向量索引写入磁盘。"""
        metadata = self._get_metadata()
        data = {}
        for mid, entry in metadata.items():
            data[mid] = entry.to_dict()
        tmp_meta = str(self._metadata_path) + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_meta, str(self._metadata_path))

        vector_store = self._get_vector_store()
        if vector_store is not None:
            vector_store.save(str(self._index_path))

    def stop(self) -> None:
        self._running = False
        with self._cv:
            self._cv.notify()
        self._flush()  # 最终刷盘
        self.wait(3000)


class MemoryStore:
    """长期记忆存储。

    M9: add() 异步 — 入队给 MemoryWriterThread，不等待磁盘 I/O。
    根据 embedding_mode 运行在不同模式：
    - api/local: FAISS 语义检索 + JSON 精确匹配
    - disabled: 仅 JSON 精确匹配
    """

    MAX_ENTRIES_DEFAULT = 1000

    def __init__(self, storage_dir: Path, dimension: int = 1536,
                 embedding_mode: str = "disabled", max_entries: int = MAX_ENTRIES_DEFAULT):
        self._mode = embedding_mode
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path = self._dir / "memory_metadata.json"
        self._index_path = self._dir / "memory_index.faiss"
        self._metadata: dict[str, MemoryEntry] = {}
        self._lock = threading.Lock()
        # M9: LRU 淘汰
        self._max_entries = max_entries
        self._access_order: list[str] = []  # 最旧在前
        self._load_metadata()
        self._vector_store = None
        if self._mode != "disabled":
            from src.transbridge.infra.vector_store import VectorStore
            self._vector_store = VectorStore(dimension=dimension)
            if self._index_path.exists():
                try:
                    self._vector_store = VectorStore.load(str(self._index_path), dimension)
                except Exception:
                    self._vector_store = VectorStore(dimension=dimension)
        # M9: 后台写入线程
        self._writer = MemoryWriterThread(
            self._dir, self._metadata_path, self._index_path,
            lambda: self._metadata,
            lambda: self._vector_store,
        )
        self._writer.start()

    # ── CRUD ──────────────────────────────────────────────

    def add(self, entry: MemoryEntry) -> str:
        """M9: 异步写入 — 仅内存操作 + 通知 writer，不等待磁盘 I/O。"""
        if not entry.memory_id:
            entry.memory_id = uuid.uuid4().hex
        with self._lock:
            self._metadata[entry.memory_id] = entry
            self._update_lru(entry.memory_id)
            if len(self._metadata) > self._max_entries:
                self._evict_lru()
        if self._vector_store and entry.embedding:
            emb = np.array(entry.embedding, dtype=np.float32)
            self._vector_store.add(emb.reshape(1, -1), [entry.memory_id])
        self._writer.enqueue()
        return entry.memory_id

    def search(self, query_vector: np.ndarray | None = None, top_k: int = 5,
               type_filter: list[str] | None = None,
               keywords: str | None = None) -> list[MemoryEntry]:
        if self._mode == "disabled" or query_vector is None or self._vector_store is None:
            return self._exact_search(type_filter, keywords)
        return self._hybrid_search(query_vector, top_k, type_filter, keywords)

    def get(self, memory_id: str) -> MemoryEntry | None:
        with self._lock:
            entry = self._metadata.get(memory_id)
            if entry:
                self._update_lru(memory_id)
            return entry

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            if memory_id in self._metadata:
                del self._metadata[memory_id]
                if memory_id in self._access_order:
                    self._access_order.remove(memory_id)
                if self._vector_store:
                    self._vector_store.remove([memory_id])
                self._writer.enqueue()
                return True
        return False

    def list_by_type(self, entry_type: str) -> list[MemoryEntry]:
        with self._lock:
            return [e for e in self._metadata.values() if e.type == entry_type]

    @property
    def count(self) -> int:
        return len(self._metadata)

    def close(self) -> None:
        """M9: 停止 writer 线程并最终刷盘。"""
        self._writer.stop()

    # ── LRU ───────────────────────────────────────────────

    def _update_lru(self, memory_id: str) -> None:
        if memory_id in self._access_order:
            self._access_order.remove(memory_id)
        self._access_order.append(memory_id)

    def _evict_lru(self) -> None:
        """M9: LRU 淘汰 — 移除最旧的条目，FAISS 标记 soft_delete。"""
        while len(self._metadata) > self._max_entries and self._access_order:
            oldest = self._access_order.pop(0)
            if oldest in self._metadata:
                del self._metadata[oldest]
                if self._vector_store:
                    self._vector_store.remove([oldest])

    # ── 内部 ──────────────────────────────────────────────

    def _exact_search(self, type_filter=None, keywords=None) -> list[MemoryEntry]:
        with self._lock:
            entries = list(self._metadata.values())
        results = []
        for e in entries:
            if type_filter and e.type not in type_filter:
                continue
            if keywords:
                kw = keywords.lower()
                if kw not in e.summary.lower() and kw not in e.content.lower():
                    continue
            results.append(e)
        return results[-20:]  # 最近 20 条

    def _hybrid_search(self, query_vector, top_k, type_filter, keywords) -> list[MemoryEntry]:
        vec_results = self._vector_store.search(query_vector, top_k)
        exact = {e.memory_id: e for e in self._exact_search(type_filter, keywords)}
        seen = set()
        merged = []
        for mem_id, _ in vec_results:
            if mem_id in exact and mem_id not in seen:
                merged.append(exact[mem_id])
                seen.add(mem_id)
        for e in exact.values():
            if e.memory_id not in seen:
                merged.append(e)
        return merged[:top_k]

    def _load_metadata(self) -> None:
        if not self._metadata_path.exists():
            return
        with open(self._metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for mid, d in data.items():
            self._metadata[mid] = MemoryEntry(
                memory_id=d.get("memory_id", mid),
                type=d.get("type", "conversation"),
                summary=d.get("summary", ""),
                content=d.get("content", ""),
                source=d.get("source", ""),
                timestamp=d.get("timestamp", ""),
                metadata=d.get("metadata", {}),
            )
