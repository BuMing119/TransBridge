# Story 02: TranslationEntryCollection 集合容器

**所属方案**: `plans/core-data-model/plan.md`
**状态**: ✔️ 已实现

## 概述

TranslationEntryCollection 是系统的唯一数据容器，所有翻译条目的增删改查操作均通过它完成。内部维护双索引（id + key），通过 AppContext 的 Qt 信号广播变更。

## 关键设计

- **双索引**: `_entries`（id → entry）+ `_key_index`（key → entry），key 字段仅为兼容历史数据
- **add(entry, overwrite=True)**: 添加/更新条目，同时更新两个索引，无独立的 update() 方法
- **filter(predicate)**: 返回筛选后的新 Collection，不修改原集合
- **导入导出**: `from_plugin()`, `from_eet_xml()`, `from_json_file()`, `from_dsd_json_file()`, `to_json_file()`, `to_dsd_json_file()`
- **迁移源合并**: `apply_xt_entries()`, `update_from_translated_plugin()` — 将外部译文合并到集合
- **信号广播**: 通过 AppContext.collection_changed 通知所有 UI 组件刷新

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/converter/translation_entry_collection.py` | TranslationEntryCollection 类 |

## 相关 ADR

- [ADR-002: Collection 数据中枢与双索引设计](../../../docs/adr/002-collection-central-data-hub.md)
