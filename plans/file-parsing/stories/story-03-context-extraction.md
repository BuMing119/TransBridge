# Story 03: 上下文提取

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

为 NPC/INFO/DIAL 记录提取额外上下文信息，丰富 AI 翻译的 Prompt 质量。

## 关键设计

- **NPCContext**: 提取 NPC 的性别、种族、职业信息，注入翻译 Prompt 辅助译名一致性
- **InfoContext**: 提取对话的说话者、情绪、关联任务（quest_formid），用于对话链上下文
- **DialContext**: 提取对话主题的任务关联
- **_build_dlbr_map()**: 构建 DIAL → (Quest, DLBR) 映射，解析对话树结构
- **editor_id 继承**: `editor_id=None` 时继承上一个有效值（REFR:FULL 除外）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/plugin/item.py` | NPCContext, InfoContext, DialContext |
| `src/transbridge/parser/plugin/plugin_with_context.py` | _extract_npc_context(), _extract_info_context(), _extract_dial_context() |
