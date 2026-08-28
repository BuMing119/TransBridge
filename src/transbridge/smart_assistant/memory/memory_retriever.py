"""记忆检索器：两阶段召回（精确关键词 + 可选语义搜索）。

语义搜索依赖外部 embedding_client（如 ai_translator.EmbeddingClient），
由调用方在构造时传入。不传入时仅使用关键词精确匹配。
"""

import numpy as np

from .memory_store import MemoryEntry, MemoryStore


class MemoryRetriever:
    """两阶段检索：精确关键词匹配 →（可选）语义向量搜索 → 合并去重。

    embedding_client 参数为可选外部依赖（如 ai_translator.EmbeddingClient），
    由 ChatWidget 在构造时传入。为 None 时仅使用 keywords 精确匹配。
    """

    def __init__(self, store: MemoryStore, embedding_client=None):
        self._store = store
        self._embedding_client = embedding_client  # m29: 外部注入，非本模块创建

    def retrieve(self, query: str, top_k: int = 5, type_filter: list[str] | None = None) -> list[MemoryEntry]:
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
