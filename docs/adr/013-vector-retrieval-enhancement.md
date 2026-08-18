# ADR-013: 向量语义检索增强（BM25 混合检索 + 增量索引 + 编码缓存）

- **状态**: 已接受
- **日期**: 2026-08-13
- **决策者**: BuMing
- **对应需求**: [FR5.12](../requirements.md)

## Context

FR5.3 已实现基于 FAISS `IndexFlatIP` 的纯向量语义术语检索（两阶段召回：精确匹配 + 语义检索）。存在三类问题：

1. **专有名词召回差**：Skyrim 的缩写、连字符、生造词（如 `Bthardamz`）向量模型编码质量差，纯向量检索漏召回/错召回。
2. **索引全量重建**：术语库任何变更都触发全量 hash 重建，术语多且频繁更新时本地模型编码慢。
3. **重复编码**：每批原文都重新编码查询向量，重复的通用句被反复编码。

FR5.12 需对 FR5.3 做三项增强：批量语义召回 + 阈值漂移统一（卫生）、增量索引 + 编码缓存（性能）、BM25 混合检索（精度）。

## Decision

### 1. BM25 实现方式：引入 rank_bm25 库

采用第三方库 `rank_bm25`（纯 Python，约 50KB，唯一硬依赖 numpy 已在项目依赖中，因 faiss-cpu/sentence-transformers/torch 均依赖 numpy）。净新增体积可忽略。

### 2. 融合排序策略：加权求和

向量分与 BM25 分各自归一化到 `[0,1]` 后加权求和：

```
fused_score = α · vec_sim + β · bm25_norm
```

- 权重 α、β 可配置，默认 `α = β = 0.5`
- 向量分：内积（归一化向量等价余弦）本身已落在 `[0,1]`
- BM25 分：按批次内 min-max 归一化到 `[0,1]`

### 3. 增量索引数据结构：ID 映射 + 标记删除（软删除）

保留 `IndexFlatIP`，维护 `term_id → 向量行位置` 映射：

- **新增**：编码新术语 → 追加向量 → 记录映射
- **修改**：标记旧向量失效 → 追加新向量 → 更新映射
- **删除**：标记失效（不回退）
- **查询**：过滤失效 ID 后取 top-k
- **压缩**：失效比例超阈值（默认 20%）或显式调用时，全量重建剔除失效向量

索引内容指纹（hash）不匹配时仍回退全量重建（现有逻辑保留）。

### 4. 编码缓存归属：term_vector_index 层 LRU

原文编码结果 LRU 缓存放 `TermVectorIndex` 层（`search`/`search_batch` 编码前查缓存），容量可配置（默认 512 条），淘汰策略 LRU。

## Alternatives Considered

| 决策点 | 方案 | 选择 | 理由 |
|--------|------|------|------|
| BM25 实现 | 自实现 vs **rank_bm25** | rank_bm25 | 成熟省心，体积影响可忽略（numpy 已有） |
| 融合排序 | **加权求和** vs 倒数排名融合 RRF | 加权求和 | 简单、可解释、权重可配；RRF 丢失分数信息 |
| 增量索引 | **ID 映射软删除** vs 部分重建 vs 换索引类型 | ID 映射软删除 | 保留 IndexFlatIP 简单高效；部分重建本质仍是全量；换索引类型风险大 |
| 缓存归属 | **term_vector_index 层** vs term_database 层 | term_vector_index 层 | 缓存与编码执行点内聚，生命周期与索引绑定 |

## Consequences

- **依赖变更**: 新增 `rank_bm25`（~50KB，numpy 已有）
- **文件变更**:
  - `src/transbridge/ai_translator/term_vector_index.py` — 增量构建（ID 映射 + 软删除 + 压缩）、编码 LRU 缓存、融合检索
  - `src/transbridge/ai_translator/term_database.py` — `match_terms_enhanced` 改用 `search_batch` + 放开 `[:10]`、接入融合检索
  - `src/transbridge/config/llm.py` — 新增融合权重、缓存容量、压缩阈值等配置项；统一相似度阈值
  - `src/transbridge/infra/embedding_client.py` — 无变更（编码接口不变）
- **接口变更**: `TermVectorIndex` 新增增量构建与融合检索方法；`match_terms_enhanced` 语义召回分支行为调整（批量 + 无 10 条上限）
- **正面**: 专有名词召回精度提升；术语库更新不再全量重建；重复编码消除
- **负面**: 引入一个第三方依赖；增量索引需维护 ID 映射与失效向量，实现复杂度上升

### 更新：2026-08-18 — 可选能力与依赖基线（已接受）

BM25/向量融合算法继续保留。`rank-bm25`、FAISS 和本地 embedding backend 的声明、锁定、构建收集与运行 capability 必须一致；系统环境偶然可导入不代表发布物可用。disabled 模式不得加载索引、模型或语料；依赖缺失时 capability 标记 degraded/unavailable，并允许不依赖检索的工作流继续。索引 manifest 记录 schema、corpus/config fingerprint 和 active/stale 状态。
