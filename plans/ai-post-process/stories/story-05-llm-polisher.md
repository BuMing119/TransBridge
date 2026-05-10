# Story 05: LLM 润色智能体

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

独立于修复阶段的 LLM 润色。专注提升译文流畅度和风格质量，无需前置问题检测即可启用。

## 关键设计

- **LLMPolisher**: 三种润色范围（all/passed/has_issues）、三种润色级别（light/moderate/aggressive）
- **PolishResult**: 含 polished_translation、confidence
- **独立启用**: 润色阶段可独立启用，不依赖问题检测结果
- **润色级别**: light（微调措辞）、moderate（改善流畅度）、aggressive（重写不符合语境的翻译）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/polisher.py` | LLMPolisher, PolishResult |
