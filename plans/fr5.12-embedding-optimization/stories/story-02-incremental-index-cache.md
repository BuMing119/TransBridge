# Story 2: 增量索引更新与编码缓存

**所属方案**: `plans/fr5.12-embedding-optimization/plan.md`
**技术模块**: backend（ai_translator）
**状态**: 已确认
**创建日期**: 2026-08-13

## 前置依赖

### 上游 Story
- Story 1（同 plan）：已完成 → 提供 `search_batch` 批量召回（本 Story 的缓存在此之上）

### 跨 Plan 依赖
无

### 引用的架构决策
- ADR-013：增量索引采用 ID 映射 + 标记删除（软删除），失效比例超 20% 全量重建；编码 LRU 缓存 512

## 验收标准

- [ ] `TermVectorIndex` 支持增量构建：新增术语追加向量并记录映射，修改术语标记旧向量失效后追加新向量
- [ ] 维护 `term_id → 向量行位置` 映射，查询时过滤失效 ID
- [ ] 失效比例超 20%（可配）触发全量重建压缩
- [ ] `search`/`search_batch` 编码前查 LRU 缓存（默认 512 条，可配）
- [ ] hash 不匹配时仍回退全量重建（保留现有逻辑）

## 数据流

```
build_index(terms, force)
  ├─ force 或 hash 不匹配 → 全量重建（现有逻辑保留）
  └─ 增量路径:
       new_texts / modified_texts / removed_texts = _diff_terms(terms)
       ├─ 新增: _append_vectors(new_texts) → FAISS.add + meta.append + row_map 记录
       ├─ 修改: 标记旧行失效 → _append_vectors(modified_texts) → 更新 meta + row_map
       ├─ 删除: _inactive_rows.add(旧行)
       └─ len(_inactive_rows)/len(meta) > 0.2 → _compact_index() 全量重建

search(query, top_k)
  → _get_query_vector(query)（LRU 缓存命中/未命中 encode）
  → FAISS.search(k = top_k×2)  ← 多取容纳失效行
  → 过滤 _inactive_rows → 阈值过滤 → 主术语去重 → 截取 top_k
```

## 关键接口

### 数据结构

```python
# term_vector_index.py — TermVectorIndex 新增字段
self._row_map: dict[str, int] = {}        # text → 当前有效向量行索引
self._inactive_rows: set[int] = set()     # 已失效的向量行索引（软删除）
self._encode_cache: OrderedDict[str, np.ndarray] = {}  # query → 向量（LRU）
```

### 函数签名

```python
def _diff_terms(self, terms: list[TermEntry]) -> tuple[list[TermEntry], list[TermEntry], set[str]]:
    """对比新旧术语，返回 (新增, 修改, 删除的 text 集合)"""

def _append_vectors(self, entries: list[tuple[str, str, str]]) -> None:
    """编码并追加向量到 FAISS + meta + row_map"""

def _compact_index(self) -> None:
    """剔除失效行，全量重建压缩索引"""

def _get_query_vector(self, query: str) -> np.ndarray:
    """LRU 缓存的查询向量获取（命中复用，未命中 encode 后缓存）"""
```

## 实现步骤

### 步骤 1: 增量构建分支

**涉及文件**: `src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- `build_index` 在 hash 变化且非 `force` 时，走增量路径（`_diff_terms` → `_append_vectors`），而非直接全量重建
- `_diff_terms` 以 `text` 为主键：新术语里没有的 text → 新增；text 相同但 translation 不同 → 修改；旧 meta 里有但新术语里没有的 text → 删除
- `_append_vectors` 复用 `_embedding_client.encode()`，`FAISS.add()` 追加，`_term_meta` append，`_row_map` 记录行索引

**边界条件**:
- 术语列表为空 → `_available = False`，返回 False（现有逻辑）
- `_embedding_client` 不可用 → 返回 False（现有逻辑）
- 增量编码失败 → 捕获异常，降级为全量重建或置 `_available = False`

**伪代码**:
```python
def build_index(self, terms, force=False):
    if not terms: ...                      # 现有
    if not self._embedding_client.available: ...
    new_hash = self._compute_term_hash(terms)
    if not force and self._try_load_index(new_hash): ...  # 现有
    if not force and self._index is not None:
        new_t, mod_t, removed = self._diff_terms(terms)
        self._apply_incremental(new_t, mod_t, removed)
        self._term_hash = new_hash
        self._save_index()
        return self._available
    # 全量重建（force 或首次）
    ...
```

**测试策略**:
- 单测：新增术语后索引行数增加；修改术语后旧行失效新行追加；删除术语后旧行失效

### 步骤 2: 软删除与失效过滤

**涉及文件**: `src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- `_row_map: dict[text → row_index]` 记录每个 text 当前有效行
- 修改术语时：`_inactive_rows.add(_row_map[text])`，追加新向量后 `_row_map[text] = 新行`
- `search`/`search_batch` 中 FAISS 检索 `k = top_k * 2`（多取容纳失效行），过滤 `_inactive_rows` 后再阈值过滤、主术语去重、截取 top_k

**边界条件**:
- 失效行过多导致过滤后不足 top_k → 返回实际可用数量
- `top_k * 2` 超过索引行数 → 用 `min(top_k*2, 索引行数)`

**伪代码**:
```python
k = min(top_k * 2, self._index.ntotal)
sims, idxs = self._index.search(query_vec, k)
for sim, idx in zip(sims[0], idxs[0]):
    if idx in self._inactive_rows: continue
    ...  # 阈值过滤 + 去重，收集到 top_k 个为止
```

**测试策略**:
- 单测：失效行不返回；过滤后结果正确

### 步骤 3: 压缩触发

**涉及文件**: `src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- 每次增量后计算 `inactive_ratio = len(_inactive_rows) / len(_term_meta)`
- 超阈值（可配，默认 0.2）→ `_compact_index()`：重建 FAISS 索引，仅保留有效行，清空 `_inactive_rows`，重建 `_row_map`

**边界条件**:
- 失效比例恰好等于阈值 → 触发（`>` 或 `>=` 需统一，建议 `>=`）

**测试策略**:
- 单测：模拟超阈值后 `_inactive_rows` 清空、索引行数减少

### 步骤 4: LRU 编码缓存

**涉及文件**: `src/transbridge/ai_translator/term_vector_index.py`（修改）

**实现要点**:
- `_get_query_vector(query)`：`_encode_cache` 命中 → `move_to_end` 返回；未命中 → encode 后存入，超容量（默认 512）`popitem(last=False)` 淘汰最旧
- `search`/`search_batch` 编码前统一走 `_get_query_vector`

**边界条件**:
- 缓存容量 0 → 禁用缓存，直接 encode
- 查询文本为空字符串 → 跳过缓存直接 encode（或返回空）

**测试策略**:
- 单测：命中时 `encode` 不被调用；超容量后最旧条目被淘汰

### 步骤 5: 回归测试

**涉及文件**: `tests/ai_translator/test_term_vector_index_incremental.py`（新建）

**实现要点**:
- mock `EmbeddingClient`（`encode` 返回固定维度向量），避免真实模型加载
- 覆盖：增量新增/修改/删除、软删除过滤、压缩触发、LRU 缓存命中/淘汰、hash 不匹配全量重建

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ai_translator/term_vector_index.py` | 修改 | 增量构建、软删除过滤、压缩、LRU 缓存 |
| `tests/ai_translator/test_term_vector_index_incremental.py` | 新建 | 增量与缓存测试 |

## 风险与注意事项

- **注意 1**: FAISS `IndexFlatIP` 支持 `add()` 追加但维度必须一致，追加前校验 `dimension`
- **注意 2**: `_row_map` 用 `text` 作主键（build_index 已按 text 去重），变体文本也作为独立行
- **注意 3**: `_save_index()` 需同时持久化 `_row_map`/`_inactive_rows`（否则重载后丢失软删除状态）；若持久化复杂，可在加载时重建 `_row_map`（按 meta 顺序），`_inactive_rows` 不持久化（重载即视为全量有效）
- **风险 1**: 增量追加导致索引文件膨胀 → 由 20% 压缩阈值兜底
- **风险 2**: 软删除状态跨会话丢失 → 选择不持久化 `_inactive_rows`，重载后按全量有效处理（正确性由 hash 校验兜底）
