# Story 01: TranslationEntry 数据类

**所属方案**: `plans/core-data-model/plan.md`
**状态**: ✔️ 已实现

## 概述

定义 TranslationEntry 数据类——TransBridge 的统一翻译数据模型。所有来源（ESP/EET/XT/DSD JSON/ParaTranz）的翻译数据均转换为此格式，下游所有操作仅依赖此单一数据模型。

## 关键设计

- `id` 格式：`{editor_id}:{form_id}|{index}~{TYPE:FIELD}`，编码了来源信息和记录类型
- `key` 与 `id` 相同，仅为兼容历史序列化数据
- `stage` 表示翻译阶段（0=未翻译, 1=AI翻译, 2=已确认），用于筛选和后处理
- `context` 格式：`{TYPE:FIELD}|{extra_info}`，如 `INFO:NAM1|quest_formid`，用于分类和上下文关联
- 工厂方法：`create_from_plugin_entry()`, `create_from_eet_entry()`, `create_from_dsd_dict()` — 各来源统一转换入口

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/converter/translation_entry.py` | TranslationEntry dataclass 定义 + 工厂方法 |

## 相关 ADR

- [ADR-001: TranslationEntry 作为统一翻译数据模型](../../../docs/adr/001-unified-translation-entry.md)
