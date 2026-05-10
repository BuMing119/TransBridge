# Story 02: 格式验证器

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

验证译文的格式正确性：SSE 特殊标签（如 `<font face='$HandwrittenFont'>`）、HTML 标签、占位符（`%d`, `%.1f`）、转义字符。

## 关键设计

- **标签完整性**: `<font>`/`<br>`/`<mag>` 等 SSE 标签是否成对
- **占位符保留**: 原文中的 `%s`/`%d`/`%.1f` 等格式化占位符在译文中是否保留
- **特殊字符**: 换行符 `\r\n`、引号转义是否与原文格式一致
- **正则匹配**: 使用正则表达式匹配 SSE 特有格式

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/quality_gate.py` | 格式验证 + 质量门禁 |
