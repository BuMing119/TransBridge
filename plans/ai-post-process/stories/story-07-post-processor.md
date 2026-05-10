# Story 07: 后处理主控器

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

协调五阶段后处理流水线（检测 → 修复 → 润色 → 裁决 → 执行），支持断点续传和各阶段独立开关。

## 关键设计

- **PostProcessor**: process() 驱动全流程
- **PostProcessorConfig**: 14 个配置字段，支持从 LLMConfig 加载
- **五阶段**: 检测(QualityGate+Consistency+Format) → 修复(LLMRefiner) → 润色(LLMPolisher,可选) → 裁决(LLMArbiter) → 执行(更新 stage)
- **各阶段独立开关**: pp_enable_consistency_check / pp_enable_format_validation / pp_enable_quality_gate / pp_enable_refinement / pp_enable_polish / pp_enable_arbitration
- **译文优先级**: 润色结果 > 修复结果 > 原始译文
- **PostProcessCheckpoint**: 断点续传，类似 AutoTranslator 的进度持久化

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/post_processor.py` | PostProcessor, PostProcessorConfig |
| `src/transbridge/ai_translator/post_processor/checkpoint.py` | PostProcessCheckpoint |
