"""长期记忆存储：FAISS 向量索引 + JSON 元数据双存储。"""

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np


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


class MemoryStore:
    """长期记忆存储。

    根据 embedding_mode 运行在不同模式：
    - api/local: FAISS 语义检索 + JSON 精确匹配
    - disabled: 仅 JSON 精确匹配
    """

    def __init__(self, storage_dir: Path, dimension: int = 1536,
                 embedding_mode: str = "disabled"):
        self._mode = embedding_mode
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path = self._dir / "memory_metadata.json"
        self._index_path = self._dir / "memory_index.faiss"
        self._metadata: dict[str, MemoryEntry] = {}
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

    # ── CRUD ──────────────────────────────────────────────

    def add(self, entry: MemoryEntry) -> str:
        if not entry.memory_id:
            entry.memory_id = uuid.uuid4().hex
        self._metadata[entry.memory_id] = entry
        if self._vector_store and entry.embedding:
            emb = np.array(entry.embedding, dtype=np.float32)
            self._vector_store.add(emb.reshape(1, -1), [entry.memory_id])
        self._save_metadata()
        if self._vector_store:
            self._vector_store.save(str(self._index_path))
        return entry.memory_id

    def search(self, query_vector: np.ndarray | None = None, top_k: int = 5,
               type_filter: list[str] | None = None,
               keywords: str | None = None) -> list[MemoryEntry]:
        if self._mode == "disabled" or query_vector is None or self._vector_store is None:
            return self._exact_search(type_filter, keywords)
        return self._hybrid_search(query_vector, top_k, type_filter, keywords)

    def get(self, memory_id: str) -> MemoryEntry | None:
        return self._metadata.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        if memory_id in self._metadata:
            del self._metadata[memory_id]
            if self._vector_store:
                self._vector_store.remove([memory_id])
            self._save_metadata()
            return True
        return False

    def list_by_type(self, entry_type: str) -> list[MemoryEntry]:
        return [e for e in self._metadata.values() if e.type == entry_type]

    @property
    def count(self) -> int:
        return len(self._metadata)

    # ── 内部 ──────────────────────────────────────────────

    def _exact_search(self, type_filter=None, keywords=None) -> list[MemoryEntry]:
        results = []
        for e in self._metadata.values():
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

    def _save_metadata(self) -> None:
        data = {}
        for mid, entry in self._metadata.items():
            data[mid] = {
                "memory_id": entry.memory_id, "type": entry.type,
                "summary": entry.summary, "content": entry.content,
                "source": entry.source, "timestamp": entry.timestamp,
                "metadata": entry.metadata,
            }
        with open(self._metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
