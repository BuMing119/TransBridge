"""
术语向量索引模块。

提供基于 FAISS 的语义检索能力，支持：
- 离线索引构建与持久化
- 在线语义召回
- 增量更新
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .term_database import TermEntry

logger = logging.getLogger(__name__)

# 延迟导入，避免未安装时崩溃
_faiss = None
_sentence_transformers = None


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


def _get_sentence_transformers():
    """延迟导入 SentenceTransformer。"""
    global _sentence_transformers
    if _sentence_transformers is None:
        try:
            from sentence_transformers import SentenceTransformer
            _sentence_transformers = SentenceTransformer
        except ImportError:
            logger.warning("sentence-transformers not installed, semantic matching disabled")
            return None
    return _sentence_transformers


# 默认模型名称
DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# 语义召回相似度阈值 (内积，归一化后 0~1)
# 低于此阈值的候选将被过滤
DEFAULT_SIMILARITY_THRESHOLD = 0.8

# 每条原文最多召回的术语数
DEFAULT_TOP_K_PER_ENTRY = 5

# 每批次术语表硬上限（防止 token 爆炸）
DEFAULT_MAX_TERMS_PER_BATCH = 50


@dataclass
class VectorSearchResult:
    """语义检索结果。"""
    term: str
    translation: str
    similarity: float  # 相似度分数 (0~1)


class TermVectorIndex:
    """
    术语向量索引。

    使用 SentenceTransformer 编码术语，FAISS 构建索引，
    支持语义相似度检索。

    持久化文件：
    - data/ai_translator/{esp_stem}/{esp_stem}_terms.faiss：FAISS 索引
    - data/ai_translator/{esp_stem}/{esp_stem}_terms_meta.json：术语元数据 (term, translation, hash)
    """

    def __init__(
        self,
        esp_path: str,
        model_name: str = DEFAULT_MODEL,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        top_k_per_entry: int = DEFAULT_TOP_K_PER_ENTRY,
    ):
        self._esp_path = esp_path
        self._model_name = model_name
        self._similarity_threshold = similarity_threshold
        self._top_k_per_entry = top_k_per_entry

        # 延迟初始化的资源
        self._model = None
        self._index = None
        self._term_meta: list[dict] = []  # [{term, translation}, ...]
        self._term_hash: str = ""  # 术语库内容 hash，用于判断是否需要重建

        # 文件路径
        from src.transbridge.paratranz.config_manager import LLMConfig
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

    def _resolve_model_path(self) -> str:
        """优先使用打包内的本地模型，回退到 HuggingFace 在线下载。"""
        import sys
        if getattr(sys, "frozen", False):
            # PyInstaller onedir：_MEIPASS 指向 EXE 所在目录
            base = sys._MEIPASS
        else:
            # 开发模式：相对于项目根目录
            base = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        local_path = os.path.join(base, "data", "models", self._model_name)
        return local_path if os.path.isdir(local_path) else self._model_name

    def _load_model(self) -> bool:
        """延迟加载 embedding 模型。"""
        if self._model is not None:
            return True

        ST = _get_sentence_transformers()
        if ST is None:
            self._init_error = "sentence-transformers not installed"
            return False

        model_path = self._resolve_model_path()
        try:
            self._model = ST(model_path)
            return True
        except Exception as e:
            self._init_error = f"Failed to load embedding model '{model_path}': {e}"
            logger.error(self._init_error)
            return False

    def _compute_term_hash(self, terms: list[TermEntry]) -> str:
        """计算术语列表的内容 hash。"""
        content = json.dumps(
            [{"t": e.term, "tr": e.translation} for e in sorted(terms, key=lambda x: x.term)],
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

        # 检查是否需要重建
        new_hash = self._compute_term_hash(terms)
        if not force and self._try_load_index(new_hash):
            self._available = True
            logger.info(f"Loaded existing vector index with {len(self._term_meta)} terms")
            return True

        # 需要构建，先加载模型
        if not self._load_model():
            return False

        faiss = _get_faiss()
        if faiss is None:
            self._init_error = "faiss not installed"
            return False

        try:
            # 编码所有术语
            term_texts = [e.term for e in terms]
            logger.info(f"Encoding {len(term_texts)} terms for vector index...")

            embeddings = self._model.encode(
                term_texts,
                normalize_embeddings=True,  # 归一化后可用内积近似余弦
                show_progress_bar=False,
            )
            embeddings = np.array(embeddings).astype("float32")

            # 构建 FAISS 索引 (IndexFlatIP = 内积索引)
            dimension = embeddings.shape[1]
            self._index = faiss.IndexFlatIP(dimension)
            self._index.add(embeddings)

            # 保存元数据
            self._term_meta = [{"term": e.term, "translation": e.translation} for e in terms]
            self._term_hash = new_hash

            # 持久化
            self._save_index()

            self._available = True
            logger.info(f"Built vector index with {len(self._term_meta)} terms (dim={dimension})")
            return True

        except Exception as e:
            self._init_error = f"Failed to build index: {e}"
            logger.error(self._init_error)
            self._available = False
            return False

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
            "model": self._model_name,
            "terms": self._term_meta,
        }
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

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
            按相似度降序排列的候选列表
        """
        if top_k is None:
            top_k = self._top_k_per_entry

        if not self._available or self._index is None:
            return []

        if not self._load_model():
            return []

        try:
            # 编码查询
            query_embedding = self._model.encode(
                [query],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            query_embedding = np.array(query_embedding).astype("float32")

            # 检索
            similarities, indices = self._index.search(query_embedding, top_k)

            results = []
            for sim, idx in zip(similarities[0], indices[0]):
                if idx < 0 or idx >= len(self._term_meta):
                    continue
                if sim < self._similarity_threshold:
                    continue
                meta = self._term_meta[idx]
                results.append(VectorSearchResult(
                    term=meta["term"],
                    translation=meta["translation"],
                    similarity=float(sim),
                ))

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

        if not self._load_model():
            return {q: [] for q in queries}

        if not queries:
            return {}

        try:
            # 批量编码
            query_embeddings = self._model.encode(
                queries,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            query_embeddings = np.array(query_embeddings).astype("float32")

            # 批量检索
            similarities, indices = self._index.search(query_embeddings, top_k)

            results = {}
            for i, query in enumerate(queries):
                query_results = []
                for sim, idx in zip(similarities[i], indices[i]):
                    if idx < 0 or idx >= len(self._term_meta):
                        continue
                    if sim < self._similarity_threshold:
                        continue
                    meta = self._term_meta[idx]
                    query_results.append(VectorSearchResult(
                        term=meta["term"],
                        translation=meta["translation"],
                        similarity=float(sim),
                    ))
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
