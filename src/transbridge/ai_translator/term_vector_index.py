"""
术语向量索引模块。

提供基于 FAISS 的语义检索能力，支持：
- 离线索引构建与持久化
- 在线语义召回
- 增量更新

Embedding 编码通过 EmbeddingClient 实现，支持本地模型和 API 服务。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ..infra.embedding_client import EmbeddingClient

if TYPE_CHECKING:
    from .term_database import TermEntry

logger = logging.getLogger(__name__)

# 延迟导入，避免未安装时崩溃
_faiss = None


def _get_faiss():
    """延迟导入 FAISS。"""
    global _faiss
    if _faiss is None:
        try:
            import faiss
            _faiss = faiss
        except ImportError:
            logger.warning("faiss not installed, semantic matching disabled")
            return None
    return _faiss


# 延迟导入 rank_bm25，避免未安装时崩溃
_bm25_okapi = None


def _get_bm25():
    """延迟导入 BM25Okapi。"""
    global _bm25_okapi
    if _bm25_okapi is None:
        try:
            from rank_bm25 import BM25Okapi
            _bm25_okapi = BM25Okapi
        except ImportError:
            logger.warning("rank_bm25 not installed, hybrid retrieval disabled")
            return None
    return _bm25_okapi


# 默认语义召回相似度阈值 (内积，归一化后 0~1)
# 低于此阈值的候选将被过滤
DEFAULT_SIMILARITY_THRESHOLD = 0.7

# 每条原文最多召回的术语数
DEFAULT_TOP_K_PER_ENTRY = 5

# 每批次术语表硬上限（防止 token 爆炸）
DEFAULT_MAX_TERMS_PER_BATCH = 50


@dataclass
class VectorSearchResult:
    """语义检索结果。"""
    term: str          # 匹配到的主术语
    translation: str   # 主术语的译文
    similarity: float  # 相似度分数 (0~1)
    matched_variant: str | None = None  # 实际匹配的变体（如果有）


class TermVectorIndex:
    """
    术语向量索引。

    使用 EmbeddingClient 编码术语，FAISS 构建索引，
    支持语义相似度检索。

    持久化文件：
    - data/ai_translator/{esp_stem}/{esp_stem}_terms.faiss：FAISS 索引
    - data/ai_translator/{esp_stem}/{esp_stem}_terms_meta.json：术语元数据 (term, translation, hash)
    """

    def __init__(
        self,
        esp_path: str,
        embedding_client: EmbeddingClient,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        top_k_per_entry: int = DEFAULT_TOP_K_PER_ENTRY,
        bm25_weight: float = 0.5,
    ):
        self._esp_path = esp_path
        self._embedding_client = embedding_client
        self._similarity_threshold = similarity_threshold
        self._top_k_per_entry = top_k_per_entry
        self._bm25_weight = bm25_weight

        # FAISS 索引和元数据
        self._index = None
        self._term_meta: list[dict] = []  # [{text, term, translation}, ...]
        self._term_hash: str = ""  # 术语库内容 hash，用于判断是否需要重建

        # 增量索引：text → 当前有效向量行索引 + 软删除标记
        self._row_map: dict[str, int] = {}
        self._inactive_rows: set[int] = set()

        # 查询编码 LRU 缓存
        self._encode_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_size: int = 512
        self._rebuild_threshold: float = 0.2

        # BM25 混合检索
        self._bm25 = None

        # 文件路径
        from transbridge.paratranz.config_manager import LLMConfig
        stem = os.path.splitext(os.path.basename(esp_path))[0]
        ai_dir = LLMConfig.get_ai_translator_dir(stem)
        self._index_path = os.path.join(ai_dir, f"{stem}_terms.faiss")
        self._meta_path = os.path.join(ai_dir, f"{stem}_terms_meta.json")

        # 可用性标记
        self._available = False
        self._init_error: str | None = None

    @property
    def available(self) -> bool:
        """向量索引是否可用。"""
        return self._available

    @property
    def init_error(self) -> str | None:
        """初始化失败时的错误信息。"""
        return self._init_error

    def _compute_term_hash(self, terms: list[TermEntry]) -> str:
        """计算术语列表的内容 hash，包含变体。"""
        content = json.dumps(
            [{"t": e.term, "tr": e.translation, "v": sorted(e.variants)}
             for e in sorted(terms, key=lambda x: x.term)],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.md5(content.encode()).hexdigest()

    def build_index(self, terms: list[TermEntry], force: bool = False) -> bool:
        """
        构建或更新向量索引。

        Args:
            terms: 术语列表
            force: 是否强制重建

        Returns:
            是否成功构建
        """
        if not terms:
            self._available = False
            return False

        # 检查 embedding client 是否可用
        if not self._embedding_client.available:
            self._init_error = f"Embedding client not available: {self._embedding_client.error_message}"
            logger.warning(self._init_error)
            return False

        # 检查是否需要重建
        new_hash = self._compute_term_hash(terms)
        if not force and self._try_load_index(new_hash):
            self._available = True
            logger.info(f"Loaded existing vector index with {len(self._term_meta)} terms")
            return True

        # 需要构建
        faiss = _get_faiss()
        if faiss is None:
            self._init_error = "faiss not installed"
            return False

        unique_entries = self._build_unique_entries(terms)

        # 增量路径：已有索引且非 force
        if not force and self._index is not None and self._term_meta:
            return self._incremental_update(unique_entries, new_hash)

        # 全量重建
        return self._full_rebuild(unique_entries, new_hash, faiss)

    def _build_unique_entries(self, terms: list[TermEntry]) -> list[tuple[str, str, str]]:
        """构建去重后的索引条目列表 (文本, 主术语, 译文)。"""
        index_entries: list[tuple[str, str, str]] = []
        for e in terms:
            index_entries.append((e.term, e.term, e.translation))
            for variant in e.variants:
                if variant:
                    index_entries.append((variant, e.term, e.translation))
        seen_texts = set()
        unique_entries = []
        for text, main_term, trans in index_entries:
            if text not in seen_texts:
                seen_texts.add(text)
                unique_entries.append((text, main_term, trans))
        return unique_entries

    def _full_rebuild(self, unique_entries, new_hash, faiss) -> bool:
        """全量编码并重建 FAISS 索引。"""
        try:
            texts_to_encode = [e[0] for e in unique_entries]
            logger.info(f"Encoding {len(texts_to_encode)} terms (including variants) for vector index...")
            embeddings = self._embedding_client.encode(texts_to_encode)
            dimension = embeddings.shape[1]
            self._index = faiss.IndexFlatIP(dimension)
            self._index.add(embeddings)

            self._term_meta = [
                {"text": text, "term": main_term, "translation": trans}
                for text, main_term, trans in unique_entries
            ]
            self._term_hash = new_hash
            self._row_map = {m["text"]: i for i, m in enumerate(self._term_meta)}
            self._inactive_rows = set()
            self._build_bm25()

            self._save_index()
            self._available = True
            logger.info(f"Built vector index with {len(self._term_meta)} entries (dim={dimension})")
            return True
        except Exception as e:
            self._init_error = f"Failed to build index: {e}"
            logger.error(self._init_error)
            self._available = False
            return False

    def _incremental_update(self, unique_entries, new_hash) -> bool:
        """增量更新：只编码新增/修改的文本，标记删除的旧向量失效。"""
        old_map = {m["text"]: (m["term"], m["translation"]) for m in self._term_meta}
        new_map = {e[0]: (e[1], e[2]) for e in unique_entries}

        to_add: list[tuple[str, str, str]] = []
        for text, main_term, trans in unique_entries:
            if text not in old_map:
                to_add.append((text, main_term, trans))
            elif old_map[text] != (main_term, trans):
                old_row = self._row_map.get(text)
                if old_row is not None:
                    self._inactive_rows.add(old_row)
                to_add.append((text, main_term, trans))

        for text in old_map:
            if text not in new_map:
                old_row = self._row_map.get(text)
                if old_row is not None:
                    self._inactive_rows.add(old_row)
                    del self._row_map[text]

        try:
            if to_add:
                embeddings = self._embedding_client.encode([t[0] for t in to_add])
                start_row = len(self._term_meta)
                self._index.add(embeddings)
                for i, (text, main_term, trans) in enumerate(to_add):
                    self._term_meta.append({"text": text, "term": main_term, "translation": trans})
                    self._row_map[text] = start_row + i

            self._term_hash = new_hash

            if self._term_meta and len(self._inactive_rows) / len(self._term_meta) > self._rebuild_threshold:
                self._compact_index()

            self._build_bm25()
            self._save_index()
            self._available = True
            logger.info(f"Incremental updated index: +{len(to_add)}, inactive={len(self._inactive_rows)}")
            return True
        except Exception as e:
            self._init_error = f"Failed to incrementally update index: {e}"
            logger.error(self._init_error)
            return self._full_rebuild(unique_entries, new_hash, _get_faiss())

    def _compact_index(self) -> None:
        """剔除失效行，全量重建压缩索引。"""
        if self._index is None or not self._term_meta:
            return
        active_meta = [m for i, m in enumerate(self._term_meta) if i not in self._inactive_rows]
        if not active_meta:
            self._index = None
            self._term_meta = []
            self._row_map = {}
            self._inactive_rows = set()
            return
        faiss = _get_faiss()
        if faiss is None:
            return
        try:
            embeddings = self._embedding_client.encode([m["text"] for m in active_meta])
            dimension = embeddings.shape[1]
            new_index = faiss.IndexFlatIP(dimension)
            new_index.add(embeddings)
            self._index = new_index
            self._term_meta = active_meta
            self._row_map = {m["text"]: i for i, m in enumerate(active_meta)}
            self._inactive_rows = set()
            logger.info(f"Compacted vector index to {len(active_meta)} active entries")
        except Exception as e:
            logger.warning(f"Compaction failed: {e}")

    def _build_bm25(self) -> None:
        """用 term 文本构建 BM25 索引（rank_bm25 缺失时置 None）。"""
        BM25Okapi = _get_bm25()
        if BM25Okapi is None:
            self._bm25 = None
            return
        tokenized = [[t for t in m["text"].split() if t] for m in self._term_meta]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    def search_hybrid(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[VectorSearchResult]:
        """BM25 + 向量加权融合检索（单条）；_bm25 缺失时退化为纯向量。"""
        if self._bm25 is None:
            return self.search(query, top_k)
        batch = self.search_hybrid_batch([query], top_k)
        return batch.get(query, [])

    def search_hybrid_batch(
        self,
        queries: list[str],
        top_k: int | None = None,
    ) -> dict[str, list[VectorSearchResult]]:
        """BM25 + 向量加权融合批量检索；_bm25 缺失时退化为纯向量。"""
        if top_k is None:
            top_k = self._top_k_per_entry
        if self._bm25 is None:
            return self.search_batch(queries, top_k)
        if not self._available or self._index is None:
            return {q: [] for q in queries}
        if not self._embedding_client.available:
            return {q: [] for q in queries}
        if not queries:
            return {}

        try:
            vec_results = self.search_batch(queries, top_k * 2)
            results: dict[str, list[VectorSearchResult]] = {}
            a = self._bm25_weight if 0.0 <= self._bm25_weight <= 1.0 else 0.5
            for q in queries:
                tokenized = [t for t in q.split() if t]
                bm25_raw = self._bm25.get_scores(tokenized) if tokenized else np.zeros(len(self._term_meta))

                # BM25 归一化到 [0,1]
                if bm25_raw.size > 0 and bm25_raw.max() > bm25_raw.min():
                    bm25_norm = (bm25_raw - bm25_raw.min()) / (bm25_raw.max() - bm25_raw.min())
                else:
                    bm25_norm = np.zeros_like(bm25_raw)

                # BM25 top-k 行索引
                if bm25_raw.size > 0:
                    bm25_top_idx = set(int(i) for i in np.argsort(-bm25_raw)[:top_k * 2])
                else:
                    bm25_top_idx = set()

                # 候选行 = 向量 top-k ∪ BM25 top-k（排除失效行）
                cand_rows: set[int] = set()
                for r in vec_results.get(q, []):
                    row = self._row_map.get(r.matched_variant or r.term)
                    if row is not None:
                        cand_rows.add(row)
                cand_rows.update(i for i in bm25_top_idx if i not in self._inactive_rows)

                # 融合打分
                scored: list[tuple[float, dict]] = []
                for row in cand_rows:
                    if row in self._inactive_rows or row >= len(self._term_meta):
                        continue
                    vec_sim = 0.0
                    for r in vec_results.get(q, []):
                        if self._row_map.get(r.matched_variant or r.term) == row:
                            vec_sim = r.similarity
                            break
                    b = float(bm25_norm[row]) if row < len(bm25_norm) else 0.0
                    fused = a * vec_sim + (1.0 - a) * b
                    scored.append((fused, self._term_meta[row]))
                scored.sort(key=lambda x: -x[0])

                # 转 VectorSearchResult（主术语去重）
                out: list[VectorSearchResult] = []
                seen: set[str] = set()
                for fused, meta in scored:
                    main_term = meta["term"]
                    if main_term in seen:
                        continue
                    seen.add(main_term)
                    matched_text = meta["text"]
                    out.append(VectorSearchResult(
                        term=main_term,
                        translation=meta["translation"],
                        similarity=float(fused),
                        matched_variant=matched_text if matched_text != main_term else None,
                    ))
                    if len(out) >= top_k:
                        break
                results[q] = out
            return results
        except Exception as e:
            logger.warning(f"Hybrid search failed: {e}")
            return self.search_batch(queries, top_k)

    def _try_load_index(self, expected_hash: str) -> bool:
        """尝试加载已存在的索引。"""
        if not os.path.exists(self._index_path) or not os.path.exists(self._meta_path):
            return False

        faiss = _get_faiss()
        if faiss is None:
            return False

        try:
            # 加载元数据
            with open(self._meta_path, encoding="utf-8") as f:
                meta = json.load(f)

            if meta.get("hash") != expected_hash:
                logger.info("Term content changed, need to rebuild index")
                return False

            self._term_meta = meta.get("terms", [])
            self._term_hash = expected_hash

            # 加载 FAISS 索引
            self._index = faiss.read_index(self._index_path)

            # 重建 row_map（按 meta 顺序）；软删除状态不持久化，重载视为全量有效
            self._row_map = {m["text"]: i for i, m in enumerate(self._term_meta)}
            self._inactive_rows = set()

            return True

        except Exception as e:
            logger.warning(f"Failed to load existing index: {e}")
            return False

    def _save_index(self) -> None:
        """持久化索引和元数据。"""
        if self._index is None:
            return

        faiss = _get_faiss()
        if faiss is None:
            return

        # 确保 data 目录存在
        os.makedirs(os.path.dirname(self._index_path), exist_ok=True)

        # 保存 FAISS 索引
        faiss.write_index(self._index, self._index_path)

        # 保存元数据
        meta = {
            "hash": self._term_hash,
            "dimension": self._embedding_client.dimension,
            "terms": self._term_meta,
        }
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _get_query_vector(self, query: str) -> np.ndarray | None:
        """获取查询向量（LRU 缓存命中复用，未命中 encode 后缓存）。"""
        if self._cache_size <= 0:
            return self._embedding_client.encode([query])[0]
        if query in self._encode_cache:
            self._encode_cache.move_to_end(query)
            return self._encode_cache[query]
        vec = self._embedding_client.encode([query])[0]
        self._encode_cache[query] = vec
        if len(self._encode_cache) > self._cache_size:
            self._encode_cache.popitem(last=False)
        return vec

    def search(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[VectorSearchResult]:
        """
        语义检索。

        Args:
            query: 查询文本（通常是原文或原文片段）
            top_k: 返回候选数量，默认使用实例配置

        Returns:
            按相似度降序排列的候选列表（按主术语去重）
        """
        if top_k is None:
            top_k = self._top_k_per_entry

        if not self._available or self._index is None:
            return []

        if not self._embedding_client.available:
            return []

        try:
            # 使用 EmbeddingClient 编码查询（LRU 缓存）
            query_vec = self._get_query_vector(query)
            if query_vec is None:
                return []
            query_embedding = query_vec.reshape(1, -1).astype("float32")

            # 检索（多取以容纳失效行）
            k = min(top_k * 2, self._index.ntotal)
            if k <= 0:
                return []
            similarities, indices = self._index.search(query_embedding, k)

            results = []
            seen_main_terms = set()  # 去重：同一主术语只返回一次

            for sim, idx in zip(similarities[0], indices[0]):
                if idx < 0 or idx >= len(self._term_meta):
                    continue
                if idx in self._inactive_rows:  # 过滤软删除的失效行
                    continue
                if sim < self._similarity_threshold:
                    continue

                meta = self._term_meta[idx]
                main_term = meta["term"]

                # 跳过已返回的主术语
                if main_term in seen_main_terms:
                    continue
                seen_main_terms.add(main_term)

                # 判断是否匹配到变体
                matched_text = meta["text"]
                matched_variant = matched_text if matched_text != main_term else None

                results.append(VectorSearchResult(
                    term=main_term,
                    translation=meta["translation"],
                    similarity=float(sim),
                    matched_variant=matched_variant,
                ))

                if len(results) >= top_k:
                    break

            return results

        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def search_batch(
        self,
        queries: list[str],
        top_k: int | None = None,
    ) -> dict[str, list[VectorSearchResult]]:
        """
        批量语义检索。

        Args:
            queries: 查询文本列表
            top_k: 每个查询返回的候选数量，默认使用实例配置

        Returns:
            {query: [VectorSearchResult, ...]}
        """
        if top_k is None:
            top_k = self._top_k_per_entry

        if not self._available or self._index is None:
            return {q: [] for q in queries}

        if not self._embedding_client.available:
            return {q: [] for q in queries}

        if not queries:
            return {}

        try:
            # 分离缓存命中/未命中，未命中的批量编码
            cached: dict[str, np.ndarray] = {}
            to_encode: list[str] = []
            for q in queries:
                if self._cache_size > 0 and q in self._encode_cache:
                    self._encode_cache.move_to_end(q)
                    cached[q] = self._encode_cache[q]
                else:
                    to_encode.append(q)

            if to_encode:
                embeddings = self._embedding_client.encode(to_encode)
                for q, vec in zip(to_encode, embeddings):
                    if self._cache_size > 0:
                        self._encode_cache[q] = vec
                        if len(self._encode_cache) > self._cache_size:
                            self._encode_cache.popitem(last=False)
                    cached[q] = vec

            query_embeddings = np.array([cached[q] for q in queries]).astype("float32")

            # 批量检索（多取以容纳失效行）
            k = min(top_k * 2, self._index.ntotal)
            if k <= 0:
                return {q: [] for q in queries}
            similarities, indices = self._index.search(query_embeddings, k)

            results = {}
            for i, query in enumerate(queries):
                query_results = []
                seen_main_terms = set()  # 去重：同一主术语只返回一次
                for sim, idx in zip(similarities[i], indices[i]):
                    if idx < 0 or idx >= len(self._term_meta):
                        continue
                    if idx in self._inactive_rows:  # 过滤软删除的失效行
                        continue
                    if sim < self._similarity_threshold:
                        continue

                    meta = self._term_meta[idx]
                    main_term = meta["term"]

                    # 跳过已返回的主术语
                    if main_term in seen_main_terms:
                        continue
                    seen_main_terms.add(main_term)

                    # 判断是否匹配到变体
                    matched_text = meta["text"]
                    matched_variant = matched_text if matched_text != main_term else None

                    query_results.append(VectorSearchResult(
                        term=main_term,
                        translation=meta["translation"],
                        similarity=float(sim),
                        matched_variant=matched_variant,
                    ))

                    if len(query_results) >= top_k:
                        break

                results[query] = query_results

            return results

        except Exception as e:
            logger.warning(f"Batch vector search failed: {e}")
            return {q: [] for q in queries}

    def delete_index_files(self) -> None:
        """删除索引文件（术语库清空或重置时调用）。"""
        for path in [self._index_path, self._meta_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    logger.warning(f"Failed to delete {path}: {e}")
