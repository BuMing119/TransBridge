# Story 06: 多集合管理

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

AppContext 的多 CollectionSlot 架构，支持同时管理多个已解析集合。

## 关键设计

- **_slots dict**: key=文件全路径, value=CollectionSlot
- **add_slot(key, slot)**: 注册 → 自动激活 → 触发 collection_changed + collection_list_changed
- **remove_slot(key)**: 移除 → 自动切换 → 触发双信号
- **activate_slot(key)**: 切换活跃集合 → collection_changed
- **向后兼容**: ctx.collection/esp_path 等属性委托到 active_slot

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/context.py` | AppContext._slots + add/remove/activate |
