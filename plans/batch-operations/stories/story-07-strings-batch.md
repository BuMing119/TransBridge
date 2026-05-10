# Story 07: Strings "全部" 批量导入

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

将 .strings 文件的译文一次性应用到所有已加载集合。通过"全部"checkbox 一键操作。

## 关键设计

- **"全部" checkbox**: 勾选后 → 所有已加载 slot 同时应用 strings 译文
- **合并逻辑**: 每个 slot 独立调用 collection.update_from_translation(strings_lookup)
- **进度反馈**: 批量操作时显示总进度

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/step1.py` | _apply_migration_sources + "全部" checkbox |
