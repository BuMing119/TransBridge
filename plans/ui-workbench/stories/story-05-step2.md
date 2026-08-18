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

## 2026-08-18 历史说明：大集合完整自动装填

本 Story 原文中的“分页”仅表示为避免 GUI 主线程长时间独占而分批创建 `QTableWidgetItem`，不表示产品层分页、按需加载或截断结果。权威增量设计由 [Project Session Persistence V2 / Story 05](../../project-session-persistence-v2/stories/story-05-projection-dirty-facades.md) 承接：首批立即显示，后续批次由 `QTimer` 自动连续调度直至全部条目装填完成；不得增加“加载更多”按钮，也不得依赖滚动触底触发下一批。本说明不改变本历史 Story 的完成状态。
