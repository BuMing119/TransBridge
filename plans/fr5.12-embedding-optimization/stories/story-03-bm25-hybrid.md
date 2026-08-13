# Story 3: BM25 混合检索

**所属方案**: `plans/fr5.12-embedding-optimization/plan.md`
**技术模块**: backend（ai_translator）
**状态**: 已确认
**创建日期**: 2026-08-13

## 前置依赖

### 上游 Story
- Story 1（同 plan）：已完成 → `search_batch` 批量向量召回（本 Story 融合检索的向量路基础）
- Story 2（同 plan）：已完成 → `TermVectorIndex` 增量构建 + LRU 缓存（本 Story 在其上叠加 BM25 索引）

### 跨 Plan 依赖
无

### 引用的架构决策
- ADR-013：BM25 引入 rank_bm25 库；融合排序加权求和 `α·vec + β·bm25`（归一化，权重可配默认 0.5/0.5）

## 验收标准

- [ ] `pyproject.toml` 新增 `rank_bm25` 依赖
- [ ] `TermVectorIndex` 构建术语库 BM25 索引（词频 + IDF）
- [ ] 融合检索：向量分与 BM25 分各自归一化到 [0,1] 后加权求和（权重可配，默认 0.5/0.5）
- [ ] `match_terms_enhanced` 接入融合检索结果
- [ ] 专有名词（缩写/连字符/生造词）召回精度提升，不破坏既有向量召回

## 数据流

```
build_index(terms) → 除 FAISS 外，用 term 文本（空格分词）构建 rank_bm25.BM25Okapi

search_hybrid_batch(queries, top_k)  ← match_terms_enhanced 调用
  对每个 query:
    向量路: FAISS.search(query_vec, top_k) → {(term, vec_score)}   [vec_score ∈ [0,1]]
    BM25 路: _bm25.get_scores(tokenize(query)) → {(term, bm25_raw)}
    归一化: bm25 分 min-max → [0,1]（全 0 时保持 0）
    融合: fused = α·vec + β·bm25  （α+β=1，默认 0.5/0.5，可配）
    排序: 按 fused 降序取 top_k
  _bm25 缺失时 → 退化为纯向量（等价 Story 1 的 search_batch）
```

## 关键接口

### 数据结构

```python
# term_vector_index.py — 新增
self._bm25 = None  # rank_bm25.BM25Okapi | None（延迟导入，缺失降级）
self._tokenize = lambda s: s.split()  # 英文术语按空格分词
```

### 函数签名

```python
def _build_bm25(self, texts: list[str]) -> None:
    """用 term 文本构建 BM25 索引；rank_bm25 未安装则置 None"""

def search_hybrid(self, query: str, top_k: int | None = None) -> list[VectorSearchResult]:
    """融合检索（单条）：向量 + BM25 加权融合"""

def search_hybrid_batch(self, queries: list[str], top_k: int | None = None) -> dict[str, list[VectorSearchResult]]:
    """融合检索（批量）：match_terms_enhanced 语义召回入口"""
```

## 实现步骤

### 步骤 1: 引入 rank_bm25 依赖

**涉及文件**: `pyproject.toml`（修改）、`src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- `pyproject.toml` dependencies 新增 `rank_bm25>=0.2.2`
- `term_vector_index.py` 延迟导入：`try: from rank_bm25 import BM25Okapi except ImportError: BM25Okapi = None`

**边界条件**:
- rank_bm25 未安装 → `_bm25 = None`，融合降级纯向量，不抛异常

**测试策略**:
- 单测：mock `ImportError` 时 `_bm25` 为 None，融合路径退化为纯向量

### 步骤 2: 构建 BM25 索引

**涉及文件**: `src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- `build_index` 在构建 FAISS 后，对 `_term_meta` 的 text 列表（空格分词）构建 `BM25Okapi`
- 增量构建时同步重建 BM25（术语文本量小，重建成本低，不做 BM25 增量）
- 分词：英文术语 `text.split()`；数字/标点随词保留（BM25Okapi 默认处理）

**边界条件**:
- 术语文本为空列表 → `_bm25 = None`
- 单字符/空字符串术语 → `split()` 产生空 token，需过滤空串

**伪代码**:
```python
tokenized = [[t for t in text.split() if t] for text in texts]
self._bm25 = BM25Okapi(tokenized) if BM25Okapi and tokenized else None
```

**测试策略**:
- 单测：build 后 `_bm25` 非 None；空术语库时 `_bm25` 为 None

### 步骤 3: 融合检索

**涉及文件**: `src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- `search_hybrid_batch`：向量路复用 `search_batch` 的候选，BM25 路 `get_scores`，两路候选取并集，各自归一化后加权求和
- 归一化：向量分已 [0,1]；BM25 分按候选集 min-max → [0,1]（max==min 时全 0）
- 权重：`α = config.bm25_weight`（β = 1 - α）

**边界条件**:
- `_bm25` 为 None → 直接返回 `search_batch` 结果（退化）
- BM25 分全为 0 → 归一化保持 0，融合分 = α·vec
- 权重非法（不在 [0,1]）→ 用默认 0.5

**伪代码**:
```python
def _fuse(self, vec_hits, query, top_k):
    bm25_raw = self._bm25.get_scores(self._tokenize(query))
    # 取 vec_hits 对应术语的 bm25 分，归一化，加权
    fused = {term: self.alpha * v + (1-self.alpha) * b for term, v, b in ...}
    return sorted(fused.items(), key=lambda x: -x[1])[:top_k]
```

**测试策略**:
- 单测：融合分 = α·vec + β·bm25（用 mock 分数验证）；归一化除零保护；缺失降级

### 步骤 4: match_terms_enhanced 接入

**涉及文件**: `src/transbridge/ai_translator/term_database.py`（修改）

**实现要点**:
- 语义召回分支从 `search_batch` 切换为 `search_hybrid_batch`（`_bm25` 可用时融合，否则等价纯向量）
- 合并逻辑不变（`if r.term not in matched` 去重 + priority 3）

**边界条件**:
- `_bm25` 缺失时行为与 Story 1 一致（不回归）

**测试策略**:
- 单测：mock `search_hybrid_batch`，断言 `match_terms_enhanced` 调用它并正确合并

### 步骤 5: 权重配置

**涉及文件**: `src/transbridge/config/llm.py`（修改）

**实现要点**:
- `LLMConfig` 新增 `bm25_weight: float = 0.5`（α）
- `_CONFIG_FIELDS` 追加 `("llm", "bm25_weight", "bm25_weight", "getfloat")`

**边界条件**:
- 未配置 → 默认 0.5

**测试策略**:
- 单测：配置持久化往返（save/load）含 `bm25_weight`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `pyproject.toml` | 修改 | 新增 `rank_bm25` 依赖 |
| `src/transbridge/ai_translator/term_vector_index.py` | 修改 | BM25 索引 + 融合检索 |
| `src/transbridge/ai_translator/term_database.py` | 修改 | 语义召回接入 `search_hybrid_batch` |
| `src/transbridge/config/llm.py` | 修改 | 新增 `bm25_weight` 配置 |
| `tests/ai_translator/test_bm25_hybrid.py` | 新建 | 融合检索测试 |

## 风险与注意事项

- **注意 1**: BM25 分词仅按空格（英文适用），中文术语会整段作为一个 token，召回效果下降——本次范围仅针对 Skyrim 英文术语，中文分词留待后续
- **注意 2**: 向量路与 BM25 路候选集合不一致，融合必须取**并集**并对缺失分数补 0，否则会漏掉只在单路命中的术语
- **注意 3**: `get_scores` 返回全语料分数（O(n)），术语量大时每 query 一次 O(n) 遍历；n 通常 < 1000，可接受
- **风险 1**: 融合可能引入噪声（BM25 字面命中但语义无关）→ 权重可配，可调低 α（增大 BM25 权重则反之）；默认 0.5 均衡
- **风险 2**: 归一化方式（min-max）对异常值敏感 → 全 0 时特殊处理，避免除零
