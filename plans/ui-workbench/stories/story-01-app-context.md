# Story 01: AppContext 全局状态管理

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

全局状态持有者，通过 Qt 信号广播状态变化。管理多 CollectionSlot，提供向后兼容的属性委托。

## 关键设计

- **AppContext(QObject)**: 信号 collection_changed/config_changed/user_changed/project_selected/collection_list_changed/navigate_to/project_list_changed
- **CollectionSlot**: label/collection/esp_path/eet_path/xt_path/migrate_count/plugin/strings_lookup
- **_slots dict**: key = 文件全路径，支持多集合管理
- **向后兼容**: esp_path/eet_path/xt_path 等属性委托到 active_slot
- **信号流程**: collection_changed → MainWindow/Step2/StatsPanel/Step3 自动刷新

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/context.py` | AppContext, CollectionSlot |
