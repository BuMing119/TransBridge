"""长期记忆存储：FAISS 向量索引 + JSON 元数据双存储。

M9: add() 异步写入 — 仅做内存操作后立即返回，由 MemoryWriterThread 后台刷盘。
"""

import json
import logging
import os
import threading
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from src.transbridge.smart_assistant.guardrails.output_validator import sanitize_for_storage

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


class MemoryWriterThread(threading.Thread):
    """M9: 后台写入线程，批量刷写记忆数据到磁盘。

    职责：
    - 在独立线程中运行，等待 MemoryStore 通知后批量将元数据 (JSON) 和向量索引
      (FAISS) 持久化到磁盘。
    - 通过 dirty flag 避免无数据变更时的无效刷盘，减少 CPU 和 I/O 浪费。

    生命周期：
    - 由 MemoryStore.__init__ 创建并启动，随 MemoryStore.close() → stop() 终止。
    - stop() 调用后会进行最后一次刷盘，然后等待线程结束 (最多 3 秒)。

    线程模型：
    - enqueue(): 由 MemoryStore 主线程 (持有 lock) 调用，设置 dirty flag 并通知
      Condition，立即返回不阻塞。
    - run(): 线程主循环，wait 在 Condition 上 (0.5s 超时)，唤醒后检查 running
      标志和 dirty flag，仅在有脏数据时执行 _flush()。
    - _flush(): 通过回调 (get_metadata_cb / get_vector_store_cb) 获取 MemoryStore
      的当前状态并写入磁盘。元数据先写临时文件再原子 rename；写入失败时清理临时
      文件并保留 dirty flag 以触发重试。
    """

    def __init__(self, storage_dir: Path, metadata_path: Path, index_path: Path,
                 get_metadata_cb, get_vector_store_cb):
        super().__init__(daemon=True)
        self._queue: deque = deque()
        self._cv = threading.Condition()
        self._storage_dir = storage_dir
        self._metadata_path = metadata_path
        self._index_path = index_path
        self._get_metadata = get_metadata_cb
        self._get_vector_store = get_vector_store_cb
        self._running = True
        # M17: dirty generation counter — 仅在有数据变更时才执行刷盘。
        # 使用计数器而非布尔值，消除 _flush() 与 enqueue() 之间的 TOCTOU 竞态：
        # enqueue() 递增计数器，_flush() 在锁内捕获当前值并归零，IO 完成后
        # 不写回，并发 enqueue() 的递增不会被覆盖。
        self._dirty_gen: int = 0

    def enqueue(self) -> None:
        """通知 writer 有数据待刷盘。递增 dirty 计数器保证下次唤醒执行 flush。"""
        with self._cv:
            self._dirty_gen += 1
            self._cv.notify()

    def run(self) -> None:
        while self._running:
            with self._cv:
                # M53: 空闲时使用更长超时 (5s)，有脏数据时用短超时 (0.5s) 以批量聚合。
                # C11 已引入 _dirty_gen 计数器避免无效刷盘，此处进一步减少空闲唤醒频率。
                timeout = 5.0 if self._dirty_gen == 0 else 0.5
                self._cv.wait(timeout=timeout)
            if not self._running:
                break
            try:
                self._flush()
            except Exception as exc:
                logger.warning("MemoryWriter 刷盘失败: %s", exc)

    def _flush(self) -> None:
        """批量将元数据和向量索引写入磁盘。

        M17: 在 CV 锁内检查 dirty_gen 计数器，无变更时跳过以节省 I/O。
        捕获当前代次后立即归零 — 并发 enqueue() 的递增不会被覆盖，
        从而消除 TOCTOU 竞态。
        M36: 写入失败时清理临时文件 (tmp_meta) 避免残留，
        并将捕获的代次加回计数器以触发重试。
        """
        with self._cv:
            if self._dirty_gen == 0:
                return
            gen = self._dirty_gen
            self._dirty_gen = 0

        try:
            metadata = self._get_metadata()
            data = {}
            for mid, entry in metadata.items():
                data[mid] = entry.to_dict()
            # C5: 持久化前脱敏 — 移除 API 密钥/Token/文件路径等敏感信息
            data = sanitize_for_storage(data)
            tmp_meta = str(self._metadata_path) + ".tmp"
            try:
                with open(tmp_meta, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_meta, str(self._metadata_path))
            except Exception:
                # M36: 清理写入失败后残留的临时文件
                try:
                    os.unlink(tmp_meta)
                except OSError:
                    pass
                raise

            vector_store = self._get_vector_store()
            if vector_store is not None:
                # M36: 向量索引也使用临时文件 + 原子 rename，避免崩溃时残留损坏文件
                tmp_index = str(self._index_path) + ".tmp"
                try:
                    vector_store.save(tmp_index)
                    os.replace(tmp_index, str(self._index_path))
                except Exception:
                    try:
                        os.unlink(tmp_index)
                    except OSError:
                        pass
                    raise
        except Exception:
            # 写入失败：将捕获的代次加回计数器，确保重试
            with self._cv:
                self._dirty_gen += gen
            raise

    def stop(self) -> None:
        self._running = False
        with self._cv:
            self._cv.notify()
        self._flush()  # 最终刷盘
        self.join(timeout=3)


class MemoryStore:
    """长期记忆存储。

    M9: add() 异步 — 入队给 MemoryWriterThread，不等待磁盘 I/O。
    根据 embedding_mode 运行在不同模式：
    - api/local: FAISS 语义检索 + JSON 精确匹配
    - disabled: 仅 JSON 精确匹配

    persist_to_disk: 若为 False，记忆仅保留在内存中，不读写磁盘。
    """

    MAX_ENTRIES_DEFAULT = 1000

    def __init__(self, storage_dir: Path, dimension: int = 1536,
                 embedding_mode: str = "disabled", max_entries: int = MAX_ENTRIES_DEFAULT,
                 persist_to_disk: bool = False):
        self._mode = embedding_mode
        self._persist_to_disk = persist_to_disk
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path = self._dir / "memory_metadata.json"
        self._index_path = self._dir / "memory_index.faiss"
        self._metadata: dict[str, MemoryEntry] = {}
        self._lock = threading.Lock()
        # M9: LRU 淘汰 — 使用 OrderedDict 实现 O(1) 访问和淘汰
        self._max_entries = max_entries
        self._access_order: OrderedDict[str, None] = OrderedDict()  # key=memory_id, 最新在末尾
        if self._persist_to_disk:
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
        # M9: 后台写入线程 — 仅在启用持久化时创建
        if self._persist_to_disk:
            self._writer = MemoryWriterThread(
                self._dir, self._metadata_path, self._index_path,
                lambda: self._metadata,
                lambda: self._vector_store,
            )
            self._writer.start()
        else:
            self._writer = None

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
            # M35: vector_store.add 必须在锁内执行，与 _evict_lru / delete 中的
            # vector_store.remove 互斥，避免 FAISS 内部状态竞态。
            if self._vector_store and entry.embedding:
                emb = np.array(entry.embedding, dtype=np.float32)
                self._vector_store.add(emb.reshape(1, -1), [entry.memory_id])
        if self._writer:
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
                self._access_order.pop(memory_id, None)  # O(1)
                if self._vector_store:
                    self._vector_store.remove([memory_id])
                if self._writer:
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
        if self._writer:
            self._writer.stop()

    # ── LRU ───────────────────────────────────────────────

    def _update_lru(self, memory_id: str) -> None:
        """m4: O(1) LRU 更新 — 使用 OrderedDict 替代 list。"""
        self._access_order[memory_id] = None  # 插入或更新 (保持在原位置)
        self._access_order.move_to_end(memory_id)  # 移至末尾 (最新)

    def _evict_lru(self) -> None:
        """M9: LRU 淘汰 — 移除最旧的条目，FAISS 标记 soft_delete。

        m4: 使用 OrderedDict.popitem(last=False) 实现 O(1) 获取最旧条目。
        """
        while len(self._metadata) > self._max_entries and self._access_order:
            oldest, _ = self._access_order.popitem(last=False)  # O(1) 弹出最旧条目
            if oldest in self._metadata:
                del self._metadata[oldest]
                if self._vector_store:
                    self._vector_store.remove([oldest])

    # ── 内部 ──────────────────────────────────────────────

    def _exact_search(self, type_filter=None, keywords=None, limit: int = 20) -> list[MemoryEntry]:
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
        return results[-limit:]  # 最近 limit 条

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
