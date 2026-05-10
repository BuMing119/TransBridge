import numpy as np

try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False


class VectorStore:
    """FAISS 向量索引统一封装。

    支持条件初始化：当 FAISS 不可用或 mode=disabled 时，调用方可选择不创建实例。
    """

    def __init__(self, dimension: int, index_path: str | None = None):
        if not _HAS_FAISS:
            raise ImportError("faiss-cpu 未安装，请执行: pip install faiss-cpu")
        self._dimension = dimension
        self._id_map: dict[int, str] = {}  # faiss_internal_id → external_id
        if index_path:
            self._index = faiss.read_index(index_path)
        else:
            self._index = faiss.IndexFlatIP(dimension)

    # ── 核心操作 ──────────────────────────────────────────

    def create_index(self, vectors: np.ndarray, ids: list[str]) -> None:
        """从零创建索引（清空已有数据）。"""
        self._index = faiss.IndexFlatIP(self._dimension)
        self._id_map.clear()
        self.add(vectors, ids)

    def add(self, vectors: np.ndarray, ids: list[str]) -> None:
        """追加向量和对应的外部 ID。"""
        if len(vectors) != len(ids):
            raise ValueError(f"向量数 ({len(vectors)}) 与 ID 数 ({len(ids)}) 不匹配")
        start_id = self._index.ntotal
        self._index.add(vectors.astype(np.float32))
        for i, ext_id in enumerate(ids):
            self._id_map[start_id + i] = ext_id

    def search(self, query_vector: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]:
        """语义检索：返回 [(external_id, similarity_score), ...]."""
        if self._index.ntotal == 0:
            return []
        q = query_vector.reshape(1, -1).astype(np.float32)
        distances, indices = self._index.search(q, min(top_k, self._index.ntotal))
        results = []
        for i in range(indices.shape[1]):
            idx = indices[0, i]
            if idx >= 0 and idx in self._id_map:
                results.append((self._id_map[idx], float(distances[0, i])))
        return results

    def remove(self, ids: list[str]) -> None:
        """软删除（从 id_map 移除，FAISS 不支持直接删除）。

        注意：被删除的向量仍占用 FAISS 索引空间，频繁删除后建议 save→load 重建。
        """
        to_remove = set(ids)
        self._id_map = {k: v for k, v in self._id_map.items() if v not in to_remove}

    # ── 持久化 ────────────────────────────────────────────

    def save(self, path: str) -> None:
        """保存 FAISS 索引到文件。"""
        faiss.write_index(self._index, path)

    @staticmethod
    def load(path: str, dimension: int) -> "VectorStore":
        """从文件加载索引。"""
        return VectorStore(dimension=dimension, index_path=path)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._id_map)

    @property
    def dimension(self) -> int:
        return self._dimension
