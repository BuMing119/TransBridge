# FR5.12 embedding 语义检索优化

**对应需求**: FR5.12（docs/requirements.md）
**技术模块**: backend（ai_translator）
**业务域**: AI 翻译 / 术语检索
**状态**: 已实现
**创建日期**: 2026-08-13

## 功能边界

### 范围内
- 批量语义召回：`match_terms_enhanced` 语义召回改用 `search_batch`，移除 `unmatched_entries[:10]` 硬上限
- 阈值漂移统一：语义相似度阈值消除 0.7/0.8 漂移，统一为单一可配置值
- 增量索引更新：术语库变更时增量更新（新增追加、修改软删除+追加），删除回退全量重建
- 编码结果缓存：原文向量编码 LRU 缓存
- BM25 混合检索：字面匹配（rank_bm25）+ 向量相似度加权融合排序

### 范围外
- 动态相似度阈值（按翻译轮次调整，P3）
- 多语言 embedding 模型选择（P3）
- 删除场景的增量处理（回退全量重建）

## Story 清单

### Story 1: 批量语义召回与阈值统一
**验收标准**:
- [ ] `match_terms_enhanced` 语义召回分支改用 `search_batch()` 批量编码检索，替代逐条 `search()`
- [ ] 移除 `unmatched_entries[:10]` 硬上限，全部未命中原文参与语义召回
- [ ] 语义相似度阈值统一：`config/llm.py` 与 `term_vector_index.py` 常量取同一默认值（0.7）
- [ ] 现有精确匹配/子串匹配行为不回归

**详细文档**: `plans/fr5.12-embedding-optimization/stories/story-01-batch-recall-threshold.md`

### Story 2: 增量索引更新与编码缓存
**验收标准**:
- [ ] `TermVectorIndex` 支持增量构建：新增术语追加向量并记录映射，修改术语标记旧向量失效后追加新向量
- [ ] 维护 `term_id → 向量行位置` 映射，查询时过滤失效 ID
- [ ] 失效比例超 20%（可配）触发全量重建压缩
- [ ] `search`/`search_batch` 编码前查 LRU 缓存（默认 512 条，可配）
- [ ] hash 不匹配时仍回退全量重建（保留现有逻辑）

**详细文档**: `plans/fr5.12-embedding-optimization/stories/story-02-incremental-index-cache.md`

### Story 3: BM25 混合检索
**验收标准**:
- [ ] `pyproject.toml` 新增 `rank_bm25` 依赖
- [ ] `TermVectorIndex` 构建术语库 BM25 索引（词频 + IDF）
- [ ] 融合检索：向量分与 BM25 分各自归一化到 [0,1] 后加权求和（权重可配，默认 0.5/0.5）
- [ ] `match_terms_enhanced` 接入融合检索结果
- [ ] 专有名词（缩写/连字符/生造词）召回精度提升，不破坏既有向量召回

**详细文档**: `plans/fr5.12-embedding-optimization/stories/story-03-bm25-hybrid.md`

## 架构依赖
- ADR-013（向量语义检索增强）：rank_bm25、加权求和融合、ID 映射软删除、LRU 缓存 512
- ADR-003（三轮翻译策略）：术语匹配在翻译流程中的位置

## 风险与回退方案
- rank_bm25 依赖失败 → 延迟导入 + 降级为纯向量检索（现有行为）
- 增量索引 ID 映射不一致 → hash 校验兜底，不匹配回退全量重建
- BM25 融合噪声增多 → 权重可配，可调低 BM25 权重或关闭融合
