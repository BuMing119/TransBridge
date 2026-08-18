"""TermVectorIndex 增量索引 + BM25 融合检索测试（FR5.12 Story 2/3）。"""
from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import patch

from transbridge.ai_translator.term_database import TermEntry
from transbridge.ai_translator.term_vector_index import (
    DEFAULT_SIMILARITY_THRESHOLD,
    TermVectorIndex,
)
from transbridge.config.llm import LLMConfig


class _FakeEmbeddingClient:
    """返回确定性归一化向量的 EmbeddingClient，记录 encode 调用。"""

    def __init__(self, dim: int = 8):
        self._dim = dim
        self.encode_calls: list[list[str]] = []

    @property
    def available(self) -> bool:
        return True

    @property
    def error_message(self) -> str | None:
        return None

    @property
    def dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str]) -> np.ndarray:
        self.encode_calls.append(list(texts))
        out = []
        for t in texts:
            seed = sum(ord(c) for c in t) % 997
            rng = np.random.RandomState(seed)
            vec = rng.rand(self._dim).astype("float32")
            vec = vec / (np.linalg.norm(vec) + 1e-8)
            out.append(vec)
        return np.array(out).astype("float32")


def _make_terms() -> list[TermEntry]:
    return [
        TermEntry(term="Dragon", translation="龙", source="test", variants=["Dragons"]),
        TermEntry(term="Whiterun", translation="白漫城", source="test", variants=[]),
        TermEntry(term="Jarl", translation="雅尔", source="test", variants=[]),
    ]


@pytest.fixture
def index(tmp_path):
    """构造 TermVectorIndex，屏蔽文件 IO。"""
    with patch.object(TermVectorIndex, "_save_index"), patch.object(TermVectorIndex, "_try_load_index", return_value=False):
        idx = TermVectorIndex(
            esp_path="TestMod.esp",
            embedding_client=_FakeEmbeddingClient(),
            similarity_threshold=0.5,
            top_k_per_entry=3,
        )
        yield idx


def test_threshold_constant_consistent():
    """Story 1 验收：阈值常量与 LLMConfig 默认一致（0.7）。"""
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.7
    assert LLMConfig().semantic_similarity_threshold == 0.7
    assert LLMConfig().bm25_weight == 0.5


def test_full_rebuild_builds_row_map(index):
    """Story 2 验收：全量构建后 _row_map 记录每个 text 的行索引。"""
    assert index.build_index(_make_terms())
    assert index.available
    assert len(index._term_meta) == 4  # Dragon + Dragons + Whiterun + Jarl
    assert index._row_map["Dragon"] == 0
    assert index._row_map["Dragons"] == 1
    assert set(index._row_map) == {"Dragon", "Dragons", "Whiterun", "Jarl"}
    assert index._inactive_rows == set()


def test_incremental_add_appends_vectors(index):
    """Story 2 验收：新增术语只追加向量，不全量重建。"""
    index.build_index(_make_terms())
    encode_count_before = len(index._embedding_client.encode_calls)

    terms = _make_terms() + [TermEntry(term="Solitude", translation="独孤城", source="test", variants=[])]
    assert index.build_index(terms)

    assert len(index._term_meta) == 5
    assert index._row_map["Solitude"] == 4
    # 增量只编码新增的 1 个 text
    assert len(index._embedding_client.encode_calls) == encode_count_before + 1


def test_incremental_modify_marks_old_inactive(index):
    """Story 2 验收：修改术语标记旧向量失效并追加新向量。"""
    index.build_index(_make_terms())
    index._rebuild_threshold = 1.0  # 禁用压缩，单独验证失效标记
    old_row = index._row_map["Whiterun"]

    terms = _make_terms()
    for e in terms:
        if e.term == "Whiterun":
            e.translation = "雪漫城"
    assert index.build_index(terms)

    assert old_row in index._inactive_rows  # 旧行失效
    assert index._row_map["Whiterun"] != old_row  # 新行
    assert index._term_meta[index._row_map["Whiterun"]]["translation"] == "雪漫城"


def test_incremental_delete_marks_inactive(index):
    """Story 2 验收：删除术语标记旧向量失效。"""
    index.build_index(_make_terms())
    index._rebuild_threshold = 1.0  # 禁用压缩，单独验证失效标记
    removed_row = index._row_map["Jarl"]

    terms = _make_terms()[:2]  # 移除 Jarl
    assert index.build_index(terms)

    assert removed_row in index._inactive_rows
    assert "Jarl" not in index._row_map


def test_search_filters_inactive_rows(index):
    """Story 2 验收：查询过滤软删除的失效行。"""
    index.build_index(_make_terms())
    # 删除 Jarl 后，Jarl 的向量行失效
    index.build_index(_make_terms()[:2])
    results = index.search("Jarl related query", top_k=3)
    # 失效行被过滤，不应返回 Jarl
    assert all(r.term != "Jarl" for r in results)


def test_cache_hit_skips_encode(index):
    """Story 2 验收：编码缓存命中时跳过 encode。"""
    index.build_index(_make_terms())
    index.search("Dragon")
    encode_count = len(index._embedding_client.encode_calls)
    index.search("Dragon")  # 命中缓存
    assert len(index._embedding_client.encode_calls) == encode_count


def test_hybrid_search_returns_results(index):
    """Story 3 验收：BM25 融合检索返回结果。"""
    index.build_index(_make_terms())
    assert index._bm25 is not None  # rank_bm25 已安装
    results = index.search_hybrid("Dragon", top_k=3)
    assert isinstance(results, list)
    assert any(r.term == "Dragon" for r in results)


def test_hybrid_fallback_without_bm25(index):
    """Story 3 验收：BM25 缺失时退化为纯向量。"""
    index.build_index(_make_terms())
    index._bm25 = None
    results = index.search_hybrid("Dragon", top_k=3)
    assert isinstance(results, list)


def test_load_index_rebuilds_row_map(tmp_path):
    """Critical 修复：加载已有索引后重建 _row_map，融合检索向量分不丢失。"""
    # 用临时目录隔离文件 IO（不 patch _save_index/_try_load_index）
    idx1 = TermVectorIndex(esp_path="LoadTest.esp", embedding_client=_FakeEmbeddingClient(), similarity_threshold=0.5)
    idx1._index_path = str(tmp_path / "test_terms.faiss")
    idx1._meta_path = str(tmp_path / "test_terms_meta.json")
    assert idx1.build_index(_make_terms())
    assert idx1._row_map

    # 第二个实例：hash 未变，走 _try_load_index 加载已有索引
    idx2 = TermVectorIndex(esp_path="LoadTest.esp", embedding_client=_FakeEmbeddingClient(), similarity_threshold=0.5)
    idx2._index_path = str(tmp_path / "test_terms.faiss")
    idx2._meta_path = str(tmp_path / "test_terms_meta.json")
    assert idx2.build_index(_make_terms())

    # Critical 修复：加载后 _row_map 已重建
    assert set(idx2._row_map) == {"Dragon", "Dragons", "Whiterun", "Jarl"}
    # 融合检索向量分不丢失（不会退化为纯 BM25）
    results = idx2.search_hybrid("Dragon", top_k=3)
    assert any(r.term == "Dragon" for r in results)
