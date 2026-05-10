# Story 03: 质量门禁

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

基于可配置阈值判定译文是否通过质量检查。汇总一致性和格式检查结果，为每个条目打分，决定 pass/fail。

## 关键设计

- **阈值配置**: 从 PostProcessorConfig 读取各检查项的权重和阈值
- **综合评分**: 一致性得分 × 权重 + 格式得分 × 权重 → 总分 vs 通过阈值
- **条目级别判定**: 每个 entry 独立评分，不因批量而互相影响
- **问题列表输出**: 未通过的条目附带问题类型和详情，供 LLMRefiner 参考

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/quality_gate.py` | 质量门禁逻辑 |
