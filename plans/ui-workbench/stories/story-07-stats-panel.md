# Story 07: 左侧集合统计面板

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

工作台左侧的集合统计面板，以树形结构展示当前集合的分类统计（各 category 的条目数、已翻译数、未翻译数）。

## 关键设计

- **树形统计**: QTreeWidget 按 context 分类层级展示
- **实时刷新**: collection_changed 信号触发 refresh
- **统计项**: 总条目数 / 已翻译 / 未翻译 / 每个分类的条目数
- **颜色编码**: 已翻译绿色、未翻译灰色、待审核黄色

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/stats_panel.py` | CollectionStatsPanel |
