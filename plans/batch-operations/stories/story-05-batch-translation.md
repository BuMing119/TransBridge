# Story 05: 批量 AI 翻译

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

跨多个插件的批量 AI 翻译。支持插件排序、独立配置、共享术语缓存。

## 关键设计

- **_BatchTranslationDialog**: 插件列表（拖拽排序 + 勾选 + 覆盖选项）
- **共享术语**: in-flight 术语缓存跨插件实时共享，Round1 翻译结果对后续插件立即可见
- **_BatchTranslationWorker**: 串行处理每个插件（内部仍并发），PluginTranslationResult 汇总
- **断点续传**: 每个插件独立 ProgressCheckpoint

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/_batch_translation_dialog.py` | 批量翻译对话框 |
| `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` | PluginTranslationResult, BatchTranslationSummary |
| `src/transbridge/ai_translator/translator.py` | in-flight 术语共享 |
