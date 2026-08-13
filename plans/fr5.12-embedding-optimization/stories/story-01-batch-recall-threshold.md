# Story 1: 批量语义召回与阈值统一

**所属方案**: `plans/fr5.12-embedding-optimization/plan.md`
**技术模块**: backend（ai_translator）
**状态**: 已确认
**创建日期**: 2026-08-13

## 前置依赖

### 上游 Story
无（本 plan 第一个 Story）

### 跨 Plan 依赖
无

### 引用的架构决策
- ADR-013（向量语义检索增强）：融合排序、增量索引、缓存的前置清理

## 验收标准

- [ ] `match_terms_enhanced` 语义召回分支改用 `search_batch()` 批量编码检索，替代逐条 `search()`
- [ ] 移除 `unmatched_entries[:10]` 硬上限，全部未命中原文参与语义召回
- [ ] 语义相似度阈值统一：`config/llm.py` 与 `term_vector_index.py` 常量取同一默认值（0.7）
- [ ] 现有精确匹配/子串匹配行为不回归

## 数据流

```
match_terms_enhanced(entries, enable_semantic, max_terms, in_flight_terms)
  ├─ 阶段1: exact_match(originals) + match_terms(originals)        [不变]
  ├─ in_flight_terms 合并                                          [不变]
  ├─ 阶段2: 语义召回                                               [改]
  │    旧: unmatched_entries[:10] → 逐条 search(original, top_k=3)
  │    新: unmatched_originals → search_batch(originals, top_k=3) 一次性批量
  └─ 优先级排序 + max_terms 硬上限截断                              [不变]
```

## 关键接口

### 已有接口（复用）

```python
# term_vector_index.py — 已存在，无需改动
def search_batch(self, queries: list[str], top_k: int | None = None) -> dict[str, list[VectorSearchResult]]:
    """批量语义检索，返回 {query: [VectorSearchResult, ...]}"""
```

### 修改接口

```python
# term_database.py — match_terms_enhanced 阶段2 语义召回分支
def match_terms_enhanced(self, entries, enable_semantic=True, max_terms=100, in_flight_terms=None) -> dict[str, str]:
    """增强版术语匹配；语义召回改用 search_batch，移除 [:10] 上限"""
```

## 实现步骤

### 步骤 1: 语义召回改批量

**涉及文件**: `src/transbridge/ai_translator/term_database.py`（修改）

**实现要点**:
- 将阶段2 的 `unmatched_entries`（list[TranslationEntry]）改为提取 `unmatched_originals`（list[str]）
- 用 `self._vector_index.search_batch(unmatched_originals, top_k=3)` 一次批量检索
- 遍历 `batch_results.values()` 合并语义术语（与现有 `semantic_match()` 方法的合并逻辑一致）
- 移除 `unmatched_entries[:10]` 切片

**边界条件**:
- `unmatched_originals` 为空 → `search_batch` 返回 `{}`，`for` 循环不执行，语义术语为空
- `search_batch` 全部无命中（相似度低于阈值）→ 每个 query 对应空列表，语义术语为空
- 语义召回术语与精确/子串命中重复 → 依赖现有 `if r.term not in matched` 去重

**伪代码**:
```python
if enable_semantic and self._vector_index and self._vector_index.available:
    unmatched_originals = [
        e.original for e in entries
        if not any(term.lower() in e.original.lower() for term in substring_matched)
    ]
    batch_results = self._vector_index.search_batch(unmatched_originals, top_k=3)
    for results in batch_results.values():
        for r in results:
            if r.term not in matched:
                matched[r.term] = r.translation
                semantic_terms.add(r.term)
                priority[r.term] = 3
```

**测试策略**:
- 单测：mock `TermVectorIndex.search_batch`，断言 `match_terms_enhanced` 调用 `search_batch`（非 `search`），传入的 queries 为全部未命中原文（数量 > 10 时不被截断）

### 步骤 2: 阈值漂移统一

**涉及文件**: `src/transbridge/config/llm.py`、`src/transbridge/ai_translator/term_vector_index.py`、`src/transbridge/ai_translator/term_database.py`（修改）

**实现要点**:
- 统一语义相似度阈值默认值为 `0.7`，消除三处漂移：
  - `config/llm.py` `semantic_similarity_threshold` 默认 0.7（已是 0.7，保持）
  - `term_vector_index.py` `DEFAULT_SIMILARITY_THRESHOLD` 0.8 → 0.7
  - `term_database.py` `_init_vector_index` 的 `getattr(..., 0.8)` fallback 0.8 → 0.7

**边界条件**:
- 用户 INI 已配置显式阈值 → 以配置值为准（`getattr` 仅在无配置时用 fallback）
- 阈值超出 [0,1] → 沿用现有行为（不做额外校验，保持现状）

**测试策略**:
- 单测：断言 `LLMConfig().semantic_similarity_threshold == 0.7` 且 `DEFAULT_SIMILARITY_THRESHOLD == 0.7`

### 步骤 3: 回归测试

**涉及文件**: `tests/infra/` 或 `tests/ai_translator/`（新增）

**实现要点**:
- 复用已有 `make_llm_config` fixture 构造 LLMConfig
- 验证精确匹配/子串匹配逻辑未被改动（通过 mock 精确路径断言）

**测试策略**:
- 单测：mock `TermVectorIndex.search_batch` 返回预设结果，断言语义术语正确合并且优先级为 3
- 回归：断言精确匹配 + 子串匹配 + in-flight 合并行为不变

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ai_translator/term_database.py` | 修改 | 语义召回改 `search_batch`，移除 `[:10]`，fallback 阈值 0.7 |
| `src/transbridge/ai_translator/term_vector_index.py` | 修改 | `DEFAULT_SIMILARITY_THRESHOLD` 0.8 → 0.7 |
| `tests/ai_translator/test_term_database_semantic.py` | 新建 | 批量召回 + 阈值一致性测试 |

## 风险与注意事项

- **注意 1**: `search_batch` 返回 `{query: [...]}`，需 `.values()` 遍历合并，勿按 query 二次去重
- **注意 2**: `matched` 字典的去重逻辑（`if r.term not in matched`）必须保留，语义术语与精确命中去重依赖它
- **风险 1**: 放开 10 条上限后语义召回计算量上升 → 由 Story 2 的编码缓存缓解
