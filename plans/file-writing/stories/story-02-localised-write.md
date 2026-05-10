# Story 02: ESP/ESM 写入（Localised 模式）

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

将译文写回本地化插件。本地化插件的字符串分为两路：int 类型（写 .strings 文件）和 RawString 类型（写 ESP 内联）。

## 关键设计

- **分流处理**: subrecord.string 是 int → PluginStringsWriter.add()；是 RawString → subrecord.set_string()
- **PluginStringsWriter**: 批量收集字符串修改 → write() 输出 .strings/.dlstrings/.ilstrings 文件
- **双路写回**: ESP 内联字符串 + 外部 strings 文件同时更新

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/plugin_writer.py` | apply_collection() + write() |
| `src/transbridge/parser/strings_file.py` | PluginStringsWriter |
