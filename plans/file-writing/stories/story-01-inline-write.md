# Story 01: ESP/ESM 写入（Inline 模式）

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

将 Collection 中的译文写回非本地化 ESP/ESM 插件。通过 PluginWriter 直接修改 subrecord 的字符串值。

## 关键设计

- **PluginWriter.apply_collection()**: 遍历插件字符串 → 构建 entry_id → Collection.get_by_key() 匹配 → subrecord.set_string()
- **entry_id 格式**: `{editor_id}:{form_id}|{index}~{rec:sub}`（与 Parser 端格式完全一致）
- **匹配策略**: 基于 entry_id 精确匹配，无模糊查找

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/plugin_writer.py` | PluginWriter 类 |
