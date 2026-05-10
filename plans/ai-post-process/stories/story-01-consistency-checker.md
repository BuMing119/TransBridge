# Story 01: 一致性检查器

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

检测翻译后处理中的一致性问题：术语翻译一致性、风格一致性、重复条目翻译一致性。

## 关键设计

- **术语一致性**: 检查同一原文术语在不同条目中是否翻译一致
- **风格一致性**: 检查对话文本的语气/正式度是否与 NPC 角色一致
- **重复检测**: 相同原文的多条记录，译文是否一致
- **问题分级**: error / warning / info 三级严重度

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/consistency_checker.py` | 一致性检查器 |
