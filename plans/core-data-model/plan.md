# 核心数据模型

> **状态**: ✔️ 已实现
> **模块**: `src/transbridge/converter/`

## 概述

定义 TransBridge 的统一翻译数据模型。所有来源（ESP/EET/XT/JSON/ParaTranz）的翻译数据在进入系统后转换为统一格式，下游所有操作仅依赖此模型。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | TranslationEntry 数据类 | ✔️ | [story-01](stories/story-01-translation-entry.md) |
| Story-02 | TranslationEntryCollection 集合容器 | ✔️ | [story-02](stories/story-02-collection.md) |
| Story-03 | Context 分类常量与导出规则 | ✔️ | [story-03](stories/story-03-context-categories.md) |
| Story-04 | DSD JSON 格式支持 | ✔️ | [story-04](stories/story-04-dsd-json.md) |
| Story-05 | 分类导出工具 | ✔️ | [story-05](stories/story-05-categorized-export.md) |

## 关键文件

- `src/transbridge/converter/translation_entry.py` — TranslationEntry 数据类
- `src/transbridge/converter/translation_entry_collection.py` — 集合容器（双索引）
- `src/transbridge/converter/translation_entry_collection_export.py` — 分类导出
- `src/transbridge/converter/context_categories.py` — Context 分类常量

## 相关 ADR

- [ADR-001: TranslationEntry 作为统一数据模型](../../docs/adr/001-unified-translation-entry.md)
- [ADR-002: Collection 数据中枢与双索引](../../docs/adr/002-collection-central-data-hub.md)

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 5/5 历史交付；V1 `id/key` 假设、来源命名空间、ExternalEntryRef、revision/provenance 与受控 mutation 尚未通过本轮合同。
- `blocked_by`：`translation-io-kernel-v2` S02、`project-session-persistence-v2` S02、`release-hardening-v2` S02。
- `superseded_by`：V1 身份与直接字段 mutation 边界由 ADR-017 和 Translation I/O Kernel V2 取代；统一 Entry/Collection 资产保留。
