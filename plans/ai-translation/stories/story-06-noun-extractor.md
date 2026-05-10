# Story 06: 专有名词抽取器

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

在 Round2 对话翻译完成后，调用 LLM 从已翻译文本中抽取专有名词（人名、地名、物品名等），自动加入动态术语库。

## 关键设计

- **NounExtractor.extract(translated_entries)**: 批量分析译文 → 提取专有名词 → 生成 TermEntry 列表
- **自动术语写入**: 抽取结果自动写入 DynamicTermDatabase（来源=auto_name/auto_dialogue）
- **LLM 调用**: 使用独立的专有名词抽取 Prompt（data/prompts/noun_extraction.toml）
- **去重**: 与已有术语比对，跳过已存在的术语

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/noun_extractor.py` | NounExtractor |
