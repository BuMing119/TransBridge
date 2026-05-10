# Story 06: DSD JSON 导入

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

从 DSD 格式 JSON 文件导入翻译条目到 Collection。支持 4 种 DSD 变体的自动识别。

## 关键设计

- **Collection.from_dsd_json_file()**: 读取 JSON → 自动识别变体 → 批量创建 TranslationEntry
- **变体识别**: 检查 JSON 对象字段（editor_id/original/index 存在性）判定变体
- **form_id 解析**: 分离 `FormID|BaseRecordPlugin` 格式为 form_id 和 plugin 名
- **错误容错**: 格式不符合任何变体 → 跳过该条目并记录警告

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/converter/translation_entry_collection.py` | from_dsd_json_file() |
| `src/transbridge/converter/translation_entry.py` | create_from_dsd_dict() |
