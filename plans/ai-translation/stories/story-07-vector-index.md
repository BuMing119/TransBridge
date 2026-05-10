# Story 07: 向量术语检索

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

基于 FAISS 的语义向量术语检索。解决精确子串匹配无法处理拼写变体、复数、同义词等问题。

## 关键设计

- **TermVectorIndex**: FAISS 索引封装，存储术语向量 + ID 映射
- **两阶段召回**: 第一阶段精确匹配 → 第二阶段 FAISS 语义检索 → 合并去重
- **索引持久化**: `data/ai_translator/{esp_stem}/{esp_stem}_terms.faiss` + `_meta.json`
- **配置开关**: `enable_semantic_match`、`semantic_similarity_threshold`、`semantic_top_k`
- **阈值控制**: 相似度低于 threshold 的结果不返回

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/term_vector_index.py` | TermVectorIndex, VectorSearchResult |
