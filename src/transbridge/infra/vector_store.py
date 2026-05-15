import threading

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
        self._lock = threading.RLock()
        self._id_map: dict[int, str] = {}  # faiss_internal_id → external_id
        self._dirty = False  # m14: 软删除标记
        if index_path:
            self._index = faiss.read_index(index_path)
        else:
            self._index = faiss.IndexFlatIP(dimension)

    # ── 核心操作 ──────────────────────────────────────────

    def create_index(self, vectors: np.ndarray, ids: list[str]) -> None:
        """从零创建索引（清空已有数据）。"""
        if len(vectors) != len(ids):
            raise ValueError(f"向量数 ({len(vectors)}) 与 ID 数 ({len(ids)}) 不匹配")
        with self._lock:
            self._index = faiss.IndexFlatIP(self._dimension)
            self._id_map.clear()
            self.add(vectors, ids)

    def add(self, vectors: np.ndarray, ids: list[str]) -> None:
        """追加向量和对应的外部 ID。"""
        if len(vectors) != len(ids):
            raise ValueError(f"向量数 ({len(vectors)}) 与 ID 数 ({len(ids)}) 不匹配")
        with self._lock:
            start_id = self._index.ntotal
            self._index.add(vectors.astype(np.float32))
            for i, ext_id in enumerate(ids):
                self._id_map[start_id + i] = ext_id

    def search(self, query_vector: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]:
        """语义检索：返回 [(external_id, similarity_score), ...]."""
        with self._lock:
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

        被删除的向量仍占用 FAISS 索引空间。调用 remove() 后自动标记 dirty=True，
        下次 save() 前自动调用 rebuild_index() 物理移除已删除向量。(m14)
        """
        with self._lock:
            to_remove = set(ids)
            self._id_map = {k: v for k, v in self._id_map.items() if v not in to_remove}
            self._dirty = True

    def rebuild_index(self) -> None:
        """m14/M54: 从当前 id_map 重建 FAISS 索引，物理移除已软删除的向量。

        M54: 直接遍历 _id_map（仅含未删除条目），跳过已软删除的条目，
        不再扫描 range(self._index.ntotal) 全部内部 ID。
        """
        with self._lock:
            if not self._dirty or self._index.ntotal == 0:
                return
            kept_vectors = []
            new_id_map = {}
            new_idx = 0
            for faiss_id, ext_id in self._id_map.items():
                vec = self._index.reconstruct(faiss_id)
                kept_vectors.append(vec)
                new_id_map[new_idx] = ext_id
                new_idx += 1
            if kept_vectors:
                new_index = faiss.IndexFlatIP(self._dimension)
                new_index.add(np.array(kept_vectors, dtype=np.float32))
                self._index = new_index
                self._id_map = new_id_map
            else:
                self._index = faiss.IndexFlatIP(self._dimension)
                self._id_map = {}
            self._dirty = False

    # ── 持久化 ────────────────────────────────────────────

    def save(self, path: str) -> None:
        """保存 FAISS 索引到文件。m14: 保存前自动重建以移除软删除向量。"""
        with self._lock:
            if self._dirty:
                self.rebuild_index()
            faiss.write_index(self._index, path)

    @staticmethod
    def load(path: str, dimension: int) -> "VectorStore":
        """从文件加载索引。"""
        return VectorStore(dimension=dimension, index_path=path)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._id_map)

    @property
    def dimension(self) -> int:
        return self._dimension
