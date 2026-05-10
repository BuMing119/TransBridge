# Story 04: DSD JSON 格式支持

**所属方案**: `plans/core-data-model/plan.md`
**状态**: ✔️ 已实现

## 概述

支持 DSD (Dynamic String Dumper) JSON 格式的导入导出。DSD 是 xEdit 脚本使用的外部翻译格式，是 TransBridge 与 xEdit/xTranslator 生态的桥梁。

## 关键设计

- **四种 DSD 变体**: 基础格式（form_id/type/string）、QUST CNAM（含 original 字段）、索引格式（form_id/type/index/string）、GMST DATA（含 editor_id）
- **from_dsd_json_file()**: 识别 JSON 结构自动选择对应的解析变体
- **to_dsd_json_file()**: 按 entry 的 context 和字段特征选择输出变体
- **form_id 格式**: `FormID|BaseRecordPlugin`（如 `000123AB|Skyrim.esm`），与 TranslationEntry.id 的双向转换

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/converter/translation_entry.py` | `create_from_dsd_dict()` 工厂方法 |
| `src/transbridge/converter/translation_entry_collection.py` | `from_dsd_json_file()`, `to_dsd_json_file()` |
