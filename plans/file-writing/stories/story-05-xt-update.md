# Story 05: XT XML 更新写入

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

更新已有 XT XML 文件中的翻译文本。支持双 List 匹配策略。

## 关键设计

- **XTWriter(parser)**: 接收已有 XT_XmlParser，遍历 .//Content/String 节点
- **双 List 匹配**: List=0（EDID == entry.id 左侧 editor_id）、List=1（EDID == [{entry.id 右侧}]）
- **REC 校验**: REC 属性值 == entry.context

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/xt_xml_writer.py` | XTWriter |
