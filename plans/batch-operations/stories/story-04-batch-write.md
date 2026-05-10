# Story 04: 批量写回

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

批量写回多个插件，支持 ESP/EET/XT 三种目标格式。

## 关键设计

- **_SlotSelectDialog**: 选择要写回的插件
- **_WriteTargetDialog**: 每个插件独立选择目标（ESP/EET XML/XT XML），EET/XT 路径从 ctx.eet_path/ctx.xt_path 预填
- **纯本地化**: 选择目录 → PluginWriter.write() pure localized 模式

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/cards/write_card.py` | _SlotSelectDialog + _WriteTargetDialog + 批量写回 |
