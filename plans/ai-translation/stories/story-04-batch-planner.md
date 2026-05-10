# Story 04: 批次规划器

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

三轮翻译策略的批次规划。根据 context 分类和 quest 分组，将待翻译条目拆分为可并发执行的批次。

## 关键设计

- **BatchPlanner.plan(entries)**: 返回 BatchPlan（三轮 × N 批次）
- **Round1**: 命名实体（NPC_/BOOK/QUST 等的 FULL/DESC）→ 全并发
- **Round2**: 对话（INFO:NAM1 按 quest_formid 分组）→ quest 间并发，quest 内串行
- **Round3**: 长文本（BOOK CNAM/DESC, INFO 长响应）→ 全并发
- **Batch 数据类**: entries + fingerprint（用于断点续传去重）
- **递归拆分**: 某批次 LLM 响应有 missing 条目时 → 对半拆分重试

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/batch_planner.py` | BatchPlanner, Batch, BatchPlan |
