# Story 07: Strings 文件导入

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

从 .strings 文件直接导入翻译条目。通过 PluginStringsLookup 解析字符串 ID→文本映射，创建 TranslationEntry。

## 关键设计

- **合并导入**: strings 文件中的译文与现有的 Collection 合并，按 ID 匹配覆盖
- **语言参数**: `strings_lang` 指定 strings 文件的语言变体（如 "chinese"→`Plugin_Chinese.strings`）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/strings_file.py` | PluginStringsLookup |
| `src/transbridge/converter/translation_entry_collection.py` | 导入合并逻辑 |
