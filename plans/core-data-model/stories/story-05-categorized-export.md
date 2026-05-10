# Story 05: 分类导出工具

**所属方案**: `plans/core-data-model/plan.md`
**状态**: ✔️ 已实现

## 概述

按 context 分类将 Collection 导出为多个 JSON 文件，用于 ParaTranz 分类上传。本地备份导出完整集合，不受用户筛选影响。

## 关键设计

- **export_to_categorized_json_files()**: 遍历 Collection，按 context_category 分流到独立文件
- **分类依据**: context_categories.py 中定义的分类规则
- **本地备份**: 始终导出完整 Collection，不受上传时的用户筛选影响
- **文件命名**: `{Category}.json`（如 `NPC_.json`, `INFO.json`, `BOOK.json`）
- **格式**: 导出为 TranslationEntry 的标准 JSON 序列化格式

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/converter/translation_entry_collection_export.py` | 分类导出工具函数 |
