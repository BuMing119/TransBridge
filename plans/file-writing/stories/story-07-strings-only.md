# Story 07: 纯本地化 Strings 输出

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

纯本地化模式：仅输出 .strings/.dlstrings/.ilstrings 文件，不修改 ESP。适用于不想改动原始 ESP 文件的场景。

## 关键设计

- **PluginWriter.write()**: 返回 `{"esp_saved": bool, "strings_written": list[Path]}`
- **纯本地化**: esp_saved=False 时仅写 strings 文件
- **语言参数**: 使用 `ctx.strings_lang` 生成文件名（如 `Plugin_Chinese.strings`）
- **输出路径**: 用户通过 `QFileDialog.getExistingDirectory` 选择输出目录

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/plugin_writer.py` | write() 返回格式 |
