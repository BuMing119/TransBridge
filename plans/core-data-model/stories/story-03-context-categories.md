# Story 03: Context 分类常量与导出规则

**所属方案**: `plans/core-data-model/plan.md`
**状态**: ✔️ 已实现

## 概述

定义 context 分类常量，将翻译条目按记录类型分组。分类规则同时驱动 AI 翻译的三轮策略和文件分类导出。

## 关键设计

- **三轮翻译分类**: Round1 实体（NPC_/BOOK/QUST 等的 FULL/DESC/SHRT）、Round2 对话（INFO:NAM1 按 quest_formid 分组）、Round3 长文本（BOOK DESC/CNAM 等）
- **导出分类**: 每种 context 前缀对应一个导出 JSON 文件（如 `{NPC_}.json`, `{BOOK}.json`），用于 ParaTranz 分类上传
- **context_categories 常量**: 定义每种记录类型 → 轮次/导出类别的映射关系

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/converter/context_categories.py` | Context 分类常量定义 |
