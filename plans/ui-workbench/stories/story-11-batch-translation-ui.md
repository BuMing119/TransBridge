# Story 11: 批量翻译 UI

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

跨多个插件的批量翻译界面。支持插件排序、选择、独立配置。

## 关键设计

- **_TranslationTargetDialog**: 选择翻译目标（当前插件/批量翻译）
- **_BatchTranslationDialog**: 插件列表（拖拽排序 + 勾选 + 覆盖选项）
- **_BatchConfigDialog**: 简化版 LLM 配置对话框
- **_BatchTranslationWorker**: 批量翻译后台线程，支持暂停/停止/断点续传
- **_BatchTranslationProgressWindow**: 总体进度 + 插件进度两级显示
- **_BatchLLMLogViewer**: 两级 Tab 结构（插件 → 批次）查看 LLM 日志
- **共享术语缓存**: in-flight 术语跨插件实时共享

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/_translation_target_dialog.py` | 目标选择 |
| `src/transbridge/ui/tools/ai_translator/_batch_translation_dialog.py` | 批量翻译对话框 |
| `src/transbridge/ui/tools/ai_translator/_batch_config_dialog.py` | 批量配置 |
| `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` | PluginTranslationResult, BatchTranslationSummary |
| `src/transbridge/ui/tools/ai_translator/_batch_translation_progress_window.py` | 批量进度 |
| `src/transbridge/ui/tools/ai_translator/_batch_llm_log_viewer.py` | 批量日志 |
