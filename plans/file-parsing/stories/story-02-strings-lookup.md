# Story 02: 本地化 Strings 文件读取

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

读取本地化插件的 `.strings`、`.dlstrings`、`.ilstrings` 文件，支持松散文件路径和 BSA 归档内文件。

## 关键设计

- **PluginStringsLookup**: 从字符串 ID（int）查找实际文本的查表结构
- **BSA 支持**: 自动检测 `Skyrim - Interface.bsa` 和 `{Plugin}.bsa`，从归档中提取 strings 文件
- **编码处理**: strings 文件使用 UTF-8 编码，特殊字符需转义处理
- **PluginStringsWriter**: 反向操作，将翻译后的文本写入 strings 文件

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/strings_file.py` | PluginStringsLookup + PluginStringsWriter |
