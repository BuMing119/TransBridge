# Story 04: EET XML 解析

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

解析 EET (Elder-scrolls-Enhanced-Translator) XML 格式的翻译文件，提取 ESP→TRADUIT 映射。

## 关键设计

- **EET_XmlParser.from_file()**: 加载并解析 XML 文件
- **find(grup, champ)**: 按 GRUP/CHAMP 筛选条目，如 `find(grup="NPC_", champ="FULL")`
- **EET_Entry**: 数据类，包含 EDID/FORMID/GRUP/CHAMP/TRADUIT/STATUS 字段
- **→ TranslationEntry**: 通过 `create_from_eet_entry()` 转换为统一格式

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/eet_parser.py` | EET_XmlParser + EET_Entry |
