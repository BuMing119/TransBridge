# Story 06: XT XML 新建构建

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

从 Collection 构建全新的 XT XML 文件。每个条目生成两个 String 节点（List=0 + List=1），兼容不同匹配策略。

## 关键设计

- **XTBuilder.build(collection, output)**: 静态方法
- **双节点**: 每条记录生成 String List="0" (EDID=editor_id) + String List="1" (EDID=[form_id|index])
- **根节点**: `<SSTXMLRessources>`

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/xt_xml_builder.py` | XTBuilder |
