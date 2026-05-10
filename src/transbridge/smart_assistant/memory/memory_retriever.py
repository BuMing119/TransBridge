"""记忆检索器：两阶段召回。"""

import numpy as np

from .memory_store import MemoryStore, MemoryEntry


class MemoryRetriever:
    """两阶段检索：精确匹配 → 语义检索 → 合并去重。"""

    def __init__(self, store: MemoryStore, embedding_client=None):
        self._store = store
        self._embedding_client = embedding_client

    def retrieve(self, query: str, top_k: int = 5,
                 type_filter: list[str] | None = None) -> list[MemoryEntry]:
        query_vector = None
        if self._embedding_client:
            try:
                emb = self._embedding_client.embed(query)
                if emb:
                    query_vector = np.array(emb, dtype=np.float32)
            except Exception:
                pass  # 嵌入失败，降级为精确匹配
        return self._store.search(
            query_vector=query_vector,
            top_k=top_k,
            type_filter=type_filter,
            keywords=query,
        )
