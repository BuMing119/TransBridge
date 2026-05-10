# Story 04: LLM 修复智能体

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

对质量门禁检测出问题的条目，使用 LLM 进行针对性修复。只修复检测到的问题，不改变已通过的译文。

## 关键设计

- **LLMRefiner**: 接收问题列表 + 原文 + 当前译文 → LLM 修复 → 返回 RefineResult
- **RefineResult**: 含 refined_translation（修复后译文）、confidence（信心度 0-1）、needs_arbitration（是否需要裁决）
- **批量处理**: refinement_batch_size 控制每次 LLM 调用的条目数
- **专注修复**: Prompt 强调只修改有问题部分，保留已通过的翻译段落

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/llm_refiner.py` | LLMRefiner, RefineResult |
