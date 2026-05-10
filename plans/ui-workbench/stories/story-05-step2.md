# Story 05: Step2 词条预览面板

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

工作台第二步：翻译条目的表格预览。支持多选 checkbox、三条件筛选（stage/category/keyword）。

## 关键设计

- **QTableWidget + UserRole**: 每条 entry 绑定 `item.setData(Qt.ItemDataRole.UserRole, entry)`
- **多选 checkbox**: 可选部分条目操作
- **筛选栏**: stage 过滤（未翻译/已翻译）、category 过滤（NPC_/INFO/BOOK等）、keyword 搜索
- **分页**: 大集合自动分页加载，避免 UI 卡顿
- **refresh**: collection_changed 信号触发自动刷新

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/step2.py` | Step2PreviewWidget |
