# Story 01: ESP/ESM/ESL 插件解析

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

解析 Bethesda 插件文件，提取所有可翻译字符串。核心入口为 PluginParser，内部调用 SSEPluginWithContext 进行深度解析。

## 关键设计

- **PluginParser.parse_plugin()**: 主入口，调度 SSEPluginWithContext.from_file() + PluginStringsLookup
- **SSEPluginWithContext**: 使用 sse-plugin-interface 解析插件底层结构，提取字符串记录和上下文
- **支持格式**: ESP, ESM, ESL（含 ESL-flagged ESP）
- **本地化/非本地化**: 自动检测插件类型，本地化插件额外加载 strings 文件

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/plugin_parser.py` | 解析入口 |
| `src/transbridge/parser/plugin/plugin_with_context.py` | SSEPluginWithContext 核心类 |
| `src/transbridge/parser/plugin/plugin_string_with_context.py` | 中间结构 |
