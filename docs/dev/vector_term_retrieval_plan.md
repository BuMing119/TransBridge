# P0-1：向量化语义术语检索实施方案

> **状态**: ✅ 已实现（2026-03-26）
>
> 实现文件：
> - `src/transbridge/ai_translator/term_vector_index.py` - FAISS 向量索引
> - `src/transbridge/ai_translator/term_database.py` - 两阶段术语匹配
> - `src/transbridge/ai_translator/translator.py` - in-flight 术语缓存
> - `src/transbridge/paratranz/config_manager.py` - 向量检索配置项

## 1. 现状分析

### 1.1 现有实现

**文件**: `src/transbridge/ai_translator/term_database.py`

```python
def match_terms(self, text_batch: list[str]) -> dict[str, str]:
    """在 text_batch 的原文中扫描匹配的术语。"""
    combined_text = "\n".join(text_batch)
    matched: dict[str, str] = {}

    for entry in self._effective_terms():  # O(n) 遍历所有术语
        if entry.case_sensitive:
            if entry.term in combined_text:  # 子串匹配
                matched[entry.term] = entry.translation
        else:
            if entry.term.lower() in combined_text.lower():
                matched[entry.term] = entry.translation

    return matched
```

### 1.2 问题

| 问题 | 示例 | 影响 |
|------|------|------|
| 拼写变体不匹配 | `Whiterun` vs `whiterun` | 需要大小写不敏感处理（已支持） |
| 复数形式不匹配 | `Dragon` vs `Dragons` | 术语库只有单数，无法匹配复数 |
| 词形变化不匹配 | `Run` vs `Running` | 动词变形无法关联 |
| 语义相近不匹配 | `mage` vs `sorcerer` | 同义词无法关联 |
| 性能问题 | 500+ 术语时 O(n) 遍历 | 每批次重复计算 |

---

## 实现差异说明

### 与原方案的差异

| 项目 | 原方案 | 实际实现 |
|------|--------|----------|
| 数据目录 | `data/{esp_stem}_*` | `data/ai_translator/{esp_stem}/{esp_stem}_*` |
| 术语匹配策略 | 仅正向子串 | 正向子串 + 冠词规范化 + 反向前缀/后缀 |
| 并发术语共享 | 无 | 新增 `_in_flight_terms` 缓存 |
| 配置项 | 4项 | 4项（enable_semantic_match, semantic_similarity_threshold, semantic_top_k, max_terms_per_batch） |
| 默认相似度阈值 | 0.8 | 0.7（降低噪声） |
| 默认 tokens | 2000 | 2500 |

### 新增功能

1. **冠词规范化匹配**：忽略术语开头的 The/A/An
2. **反向匹配**：原文是术语的词边界前缀或后缀时也可命中
3. **in-flight 术语缓存**：Round1 翻译完成后立即写入，供并发批次使用
4. **流式日志增强**：输出术语库加载情况、匹配结果、提示词内容

---

## 2. 方案设计

### 2.1 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                     TermDatabaseManager (升级后)                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  离线阶段（术语库加载/更新时）                                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  TermEntry.list → SentenceTransformer.encode → embeddings   │   │
│  │                           ↓                                  │   │
│  │              FAISS IndexFlatIP.build                         │   │
│  │                           ↓                                  │   │
│  │         持久化: data/{esp_stem}_terms.faiss                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  在线阶段（翻译批次处理时）                                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  batch_texts → encode → FAISS.search(top_k=20)              │   │
│  │                           ↓                                  │   │
│  │              语义召回候选术语 (semantic_candidates)           │   │
│  │                           ↓                                  │   │
│  │  ┌────────────────────────────────────────────────────┐     │   │
│  │  │  精确匹配 (existing exact_match)                    │     │   │
│  │  │           ↓                                         │     │   │
│  │  │  子串扫描 (existing match_terms logic)              │     │   │
│  │  │           ↓                                         │     │   │
│  │  │  语义召回 (新增，带相似度阈值过滤)                   │     │   │
│  │  │           ↓                                         │     │   │
│  │  │  三路去重合并 → matched_terms                       │     │   │
│  │  └────────────────────────────────────────────────────┘     │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

| 组件 | 职责 | 文件 |
|------|------|------|
| `TermVectorIndex` | FAISS 索引构建/持久化/检索 | `ai_translator/term_vector_index.py` (新增) |
| `TermDatabaseManager` | 集成向量检索，双路融合 | `ai_translator/term_database.py` (修改) |

### 2.3 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| Embedding 模型 | `paraphrase-multilingual-MiniLM-L12-v2` | 多语言支持、轻量(~400MB)、质量足够 |
| 向量索引 | `faiss-cpu` | 本地部署友好、无需 GPU |
| 相似度度量 | 内积 (IP) | 对归一化向量等价于余弦相似度 |

---

## 3. 详细设计

### 3.1 新增文件: `term_vector_index.py`

```python
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
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .term_database import TermEntry

# 延迟导入，避免未安装时崩溃
_faiss = None
_sentence_transformers = None


def _get_faiss():
    global _faiss
    if _faiss is None:
        import faiss
        _faiss = faiss
    return _faiss


def _get_sentence_transformers():
    global _sentence_transformers
    if _sentence_transformers is None:
        from sentence_transformers import SentenceTransformer
        _sentence_transformers = SentenceTransformer
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
    - data/{esp_stem}_terms.faiss：FAISS 索引
    - data/{esp_stem}_terms_meta.json：术语元数据 (term, translation, hash)
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
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        data_dir = ParatranzConfig.get_data_dir()
        stem = os.path.splitext(os.path.basename(esp_path))[0]
        self._index_path = os.path.join(data_dir, f"{stem}_terms.faiss")
        self._meta_path = os.path.join(data_dir, f"{stem}_terms_meta.json")

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

    def _load_model(self) -> bool:
        """延迟加载 embedding 模型。"""
        if self._model is not None:
            return True
        try:
            ST = _get_sentence_transformers()
            self._model = ST(self._model_name)
            return True
        except Exception as e:
            self._init_error = f"Failed to load embedding model: {e}"
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

        if not self._load_model():
            return False

        # 检查是否需要重建
        new_hash = self._compute_term_hash(terms)
        if not force and self._try_load_index(new_hash):
            self._available = True
            return True

        try:
            # 编码所有术语
            term_texts = [e.term for e in terms]
            embeddings = self._model.encode(
                term_texts,
                normalize_embeddings=True,  # 归一化后可用内积近似余弦
                show_progress_bar=False,
            )
            embeddings = np.array(embeddings).astype("float32")

            # 构建 FAISS 索引 (IndexFlatIP = 内积索引)
            faiss = _get_faiss()
            dimension = embeddings.shape[1]
            self._index = faiss.IndexFlatIP(dimension)
            self._index.add(embeddings)

            # 保存元数据
            self._term_meta = [{"term": e.term, "translation": e.translation} for e in terms]
            self._term_hash = new_hash

            # 持久化
            self._save_index()

            self._available = True
            return True

        except Exception as e:
            self._init_error = f"Failed to build index: {e}"
            self._available = False
            return False

    def _try_load_index(self, expected_hash: str) -> bool:
        """尝试加载已存在的索引。"""
        if not os.path.exists(self._index_path) or not os.path.exists(self._meta_path):
            return False

        try:
            # 加载元数据
            with open(self._meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("hash") != expected_hash:
                return False

            self._term_meta = meta.get("terms", [])
            self._term_hash = expected_hash

            # 加载 FAISS 索引
            faiss = _get_faiss()
            self._index = faiss.read_index(self._index_path)

            return True

        except Exception:
            return False

    def _save_index(self) -> None:
        """持久化索引和元数据。"""
        if self._index is None:
            return

        # 保存 FAISS 索引
        faiss = _get_faiss()
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

        except Exception:
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

        except Exception:
            return {q: [] for q in queries}
```

### 3.2 修改文件: `term_database.py`

在 `TermDatabaseManager` 中集成向量检索：

```python
# 新增导入
from .term_vector_index import TermVectorIndex, VectorSearchResult

class TermDatabaseManager:
    def __init__(self, ...):
        # ... 现有代码 ...
        self._vector_index: TermVectorIndex | None = None

    def load_all(self) -> dict[str, str]:
        """按 term_priority 顺序加载并合并。"""
        self._merged_terms = self._load_all_with_metadata()

        # 初始化向量索引
        self._init_vector_index()

        return {e.term: e.translation for e in self._merged_terms}

    def _init_vector_index(self) -> None:
        """初始化向量索引（延迟构建，失败时降级）。"""
        try:
            self._vector_index = TermVectorIndex(self._esp_path)
            success = self._vector_index.build_index(self._merged_terms)
            if success:
                self._load_log.append(("vector_index", len(self._merged_terms), None))
            else:
                self._load_log.append(("vector_index", 0, self._vector_index.init_error))
        except ImportError:
            self._load_log.append(("vector_index", 0, "faiss/sentence-transformers not installed"))
            self._vector_index = None

    def rebuild_vector_index(self) -> bool:
        """手动重建向量索引（术语库更新后调用）。"""
        if self._vector_index is None:
            return False
        return self._vector_index.build_index(self._effective_terms(), force=True)

    def semantic_match(
        self,
        text_batch: list[str],
        top_k: int = 5,
    ) -> dict[str, str]:
        """
        语义召回术语。

        对每条原文进行语义检索，返回相似度超过阈值的术语。
        仅在向量索引可用时工作，否则返回空 dict。

        Args:
            text_batch: 原文列表
            top_k: 每条原文召回的候选数（默认 5）

        Returns:
            {term: translation} 合并后的术语表
        """
        if self._vector_index is None or not self._vector_index.available:
            return {}

        # 批量检索
        batch_results = self._vector_index.search_batch(text_batch, top_k=top_k)

        # 合并去重
        matched: dict[str, str] = {}
        for results in batch_results.values():
            for r in results:
                # 只保留每个术语的最佳匹配
                if r.term not in matched:
                    matched[r.term] = r.translation

        return matched

    def match_terms_enhanced(
        self,
        entries: list[TranslationEntry],
        enable_semantic: bool = True,
        max_terms: int = 50,
    ) -> dict[str, str]:
        """
        增强版术语匹配：两阶段召回策略。

        阶段1：子串扫描 - 找"明确出现"的术语
        阶段2：语义召回 - 为未命中原文补充"语义相关"的术语

        Args:
            entries: 翻译条目列表（需要 original 字段）
            enable_semantic: 是否启用语义召回
            max_terms: 术语表硬上限（防止 token 爆炸）

        Returns:
            {term: translation} 合并后的术语表
        """
        originals = [e.original for e in entries]

        # 阶段1：精确匹配 + 子串扫描
        exact_matched = self.exact_match(originals)
        substring_matched = self.match_terms(originals)

        # 合并基础匹配
        matched = {**exact_matched, **substring_matched}

        # 阶段2：语义召回（仅对子串未命中的原文）
        if enable_semantic and self._vector_index and self._vector_index.available:
            # 找出没有子串命中的原文
            unmatched_entries = [
                e for e in entries
                if not any(
                    term.lower() in e.original.lower()
                    for term in substring_matched
                )
            ]

            # 对未命中原文做语义检索，补充高置信术语
            semantic_matched = {}
            for entry in unmatched_entries[:10]:  # 最多处理 10 条
                results = self._vector_index.search(entry.original, top_k=3)
                for r in results:
                    if r.term not in matched:
                        semantic_matched[r.term] = r.translation

            # 合并语义召回
            matched.update(semantic_matched)

        # 硬上限保护
        if len(matched) > max_terms:
            # 优先保留精确匹配和子串匹配
            matched = dict(list(matched.items())[:max_terms])

        return matched
```

### 3.3 修改文件: `translator.py`

在 `_run_batch` 中使用增强版匹配：

```python
# 在 _run_batch 方法中，替换原有的 match_terms 调用

def _run_batch(self, batch: Batch, ...) -> dict[str, str]:
    # ... 前置代码 ...

    # 术语匹配（两阶段增强版）
    matched_terms = self._term_mgr.match_terms_enhanced(
        entries=batch.entries,  # 传入完整条目，支持按原文精准召回
        enable_semantic=self._config.enable_semantic_match,  # 新增配置项
        max_terms=self._config.max_terms_per_batch,  # 术语表上限
    )

    # ... 后续代码 ...
```

### 3.4 配置项扩展

在 `LLMConfig` 中添加：

```python
# paratranz/config_manager.py

@dataclass
class LLMConfig:
    # ... 现有字段 ...

    # 向量检索配置
    enable_semantic_match: bool = True  # 是否启用语义召回
    semantic_similarity_threshold: float = 0.8  # 相似度阈值（高阈值减少噪声）
    semantic_top_k: int = 5  # 每条原文召回的候选数
    max_terms_per_batch: int = 50  # 每批次术语表硬上限
```

---

## 4. 依赖更新

### 4.1 requirements.txt

```
# 现有依赖
PyQt6>=6.5
openpyxl>=3.0
pandas>=2.0
openai>=1.0
anthropic>=0.20
sse-plugin-interface>=0.1

# 新增依赖
sentence-transformers>=2.2
faiss-cpu>=1.7
numpy>=1.21
```

### 4.2 可选依赖处理

```python
# 在 term_vector_index.py 中已做延迟导入处理
# 若依赖未安装，_available=False，自动降级到原有匹配逻辑

def _get_faiss():
    global _faiss
    if _faiss is None:
        try:
            import faiss
            _faiss = faiss
        except ImportError:
            return None
    return _faiss
```

---

## 5. 文件变更清单

| 文件 | 操作 | 改动内容 |
|------|------|----------|
| `ai_translator/term_vector_index.py` | **新增** | `TermVectorIndex` 类 |
| `ai_translator/term_database.py` | 修改 | 集成向量检索、新增 `match_terms_enhanced` |
| `ai_translator/translator.py` | 修改 | 使用增强版匹配 |
| `paratranz/config_manager.py` | 修改 | 新增配置项 |
| `requirements.txt` | 修改 | 新增依赖 |

---

## 6. 测试计划

### 6.1 单元测试

```python
# tests/test_term_vector_index.py

def test_build_index():
    """测试索引构建"""
    terms = [
        TermEntry(term="Dragon", translation="龙"),
        TermEntry(term="Whiterun", translation="白漫城"),
        TermEntry(term="Jarl", translation="雅尔"),
    ]
    index = TermVectorIndex("test.esp")
    assert index.build_index(terms)
    assert index.available

def test_semantic_search():
    """测试语义召回"""
    index = TermVectorIndex("test.esp")
    index.build_index([...])

    # 测试复数形式
    results = index.search("Dragons attacked the village")
    assert any(r.term == "Dragon" for r in results)

    # 测试词形变化
    results = index.search("running to Whiterun")
    assert any(r.term == "Whiterun" for r in results)

def test_similarity_threshold():
    """测试相似度阈值过滤"""
    index = TermVectorIndex("test.esp", similarity_threshold=0.8)
    # 高阈值应该过滤掉低相似度结果
    results = index.search("completely unrelated text")
    assert len(results) == 0

def test_top_k_limit():
    """测试每条原文召回数量限制"""
    index = TermVectorIndex("test.esp", top_k_per_entry=3)
    results = index.search("Dragons in Whiterun")
    assert len(results) <= 3

def test_fallback_without_deps():
    """测试依赖缺失时的降级"""
    # 模拟 ImportError
    with patch.dict(sys.modules, {"faiss": None, "sentence_transformers": None}):
        index = TermVectorIndex("test.esp")
        assert not index.available
```

### 6.2 集成测试

```python
# tests/test_term_database_manager.py

def test_match_terms_enhanced():
    """测试两阶段召回"""
    mgr = TermDatabaseManager(config, esp_path)
    mgr.load_all()

    entries = [
        TranslationEntry(original="The Dragons are attacking Whiterun!", ...),
        TranslationEntry(original="I need to buy some potions.", ...),
    ]
    matched = mgr.match_terms_enhanced(entries)

    # 应同时匹配 Dragon 和 Whiterun（子串命中）
    assert "Dragon" in matched
    assert "Whiterun" in matched

def test_max_terms_limit():
    """测试术语表硬上限"""
    mgr = TermDatabaseManager(config, esp_path)
    mgr.load_all()

    entries = [TranslationEntry(original=f"Text {i} with Dragon", ...) for i in range(100)]
    matched = mgr.match_terms_enhanced(entries, max_terms=10)

    # 不应超过上限
    assert len(matched) <= 10

def test_vector_index_persistence():
    """测试索引持久化"""
    mgr1 = TermDatabaseManager(config, esp_path)
    mgr1.load_all()

    # 第二次加载应该复用已有索引
    mgr2 = TermDatabaseManager(config, esp_path)
    mgr2.load_all()

    assert mgr2._vector_index.available
```

---

## 7. 实施步骤

### Phase 1: 基础设施（1天）

1. 创建 `term_vector_index.py`
2. 实现 `TermVectorIndex` 类核心逻辑
3. 编写单元测试

### Phase 2: 集成（1天）

4. 修改 `term_database.py`，集成向量索引
5. 实现 `match_terms_enhanced` 方法
6. 修改 `translator.py`，使用增强版匹配

### Phase 3: 配置与持久化（0.5天）

7. 扩展 `LLMConfig` 配置项
8. 实现索引版本控制（hash 校验）

### Phase 4: 测试与优化（1.5天）

9. 编写集成测试
10. 性能测试（大术语库场景）
11. 阈值调优

---

## 8. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 依赖安装失败 | 延迟导入 + 自动降级，不影响原有功能 |
| 首次构建慢 | 异步构建 + 进度提示，或预构建脚本 |
| 模型下载慢 | 支持离线模型路径配置 |
| 内存占用大 | 使用轻量模型，限制索引大小 |
| 召回噪声多 | 可配置相似度阈值，默认 0.75 |

---

## 9. Token 消耗分析

### 两阶段策略的 token 控制

```
批次处理流程：
┌─────────────────────────────────────────────────────────┐
│ 50条原文                                                │
│     ↓                                                   │
│ 阶段1: 子串扫描 → 命中 ~15-25 个术语                     │
│     ↓                                                   │
│ 阶段2: 语义召回（仅对未命中原文）                        │
│        → 最多 10 条原文 × 3 候选 = ~30 个候选            │
│        → 去重后新增 ~5-15 个术语                         │
│     ↓                                                   │
│ 合并: ~20-40 个术语                                      │
│ 硬上限: max_terms=50                                     │
│     ↓                                                   │
│ Prompt 注入: ~100-250 tokens                            │
└─────────────────────────────────────────────────────────┘
```

### 与原方案对比

| 指标 | 原方案（子串） | 新方案（两阶段） |
|------|---------------|-----------------|
| 术语表大小 | ~20个 | ~30-50个 |
| Prompt token | ~100 | ~150-250 |
| 召回覆盖率 | ~60% | ~90%+ |
| **增量 token** | 基准 | **+50-150** |

**结论：token 增量可控（每批 +50-150），覆盖率显著提升。**

---

## 10. 后续优化方向

1. **增量更新**：术语库变化时只更新新增条目的向量
2. **多语言支持**：根据原文语言选择合适的 embedding 模型
3. **混合检索**：BM25 + 向量检索的融合排序
4. **缓存优化**：对高频原文缓存检索结果
5. **动态阈值**：根据批次类型调整相似度阈值
