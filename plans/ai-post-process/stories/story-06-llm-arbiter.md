# Story 06: LLM 裁决智能体

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

对修复和润色结果做最终裁决。判定每条修改是否采纳（pass/reject/pending），确保后处理不会引入新的质量问题。

## 关键设计

- **LLMArbiter**: 裁决修复/润色结果，输出 ArbiterDecision
- **ArbiterDecision**: verdict（pass/reject/pending）+ reason + confidence
- **ArbitrationContext**: 含原文、初始译文、修改后译文、修改理由
- **快速判定**: 无需 LLM 即可处理明确场景（如 confidence > 0.9 自动 pass、无实际修改自动跳过）
- **严格模式**: uncertain → reject（保守策略）；普通模式：uncertain → pending（人工复核）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/llm_arbiter.py` | LLMArbiter, ArbiterDecision, ArbitrationContext |
