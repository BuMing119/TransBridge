# Story 04: Step1 源文件解析面板

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

工作台第一步：选择并解析源文件。支持批量 ESP 选择、迁移源追加（EET/XT/Strings）、JSON 导入。

## 关键设计

- **多文件选择**: QFileDialog.getOpenFileNames 批量选择 ESP
- **批量解析**: 多个 ESP 独立解析为多个 CollectionSlot
- **迁移源追加**: _apply_migration_sources() 追加 EET/XT/Strings 到已加载集合
- **Strings "全部"**: 一键应用到所有已加载集合
- **JSON 导入**: "导入JSON" 按钮 → 文件对话框 → TranslationEntryCollection.from_json_file()
- **部分锁定**: 迁移源按钮根据 slot 状态启用/禁用

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/step1.py` | Step1SourceWidget |
