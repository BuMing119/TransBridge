# Story 05: XT XML 解析

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

解析 XT (xTranslator) XML 格式的翻译文件，支持双 List 匹配策略（List=0 按 EDID，List=1 按 form_id）。

## 关键设计

- **XT_XmlParser.from_file()**: 加载并解析 XT XML
- **XT_Entry**: 数据类，含 REC/EDID/List/Source/Dest 字段
- **双 List 匹配**: List=0（EDID == entry.id 左侧）、List=1（EDID == [{entry.id 右侧}]）两种匹配策略
- **→ TranslationEntry**: 通过 `create_from_xt_entry()` 转换，支持双语种（Dest 字段）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/xt_parser.py` | XT_XmlParser + XT_Entry |
