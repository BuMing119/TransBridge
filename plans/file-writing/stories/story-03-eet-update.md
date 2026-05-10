# Story 03: EET XML 更新写入

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

更新已有 EET XML 文件中的翻译文本。基于已有 EET_XmlParser 实例，匹配并修改 TRADUIT 节点。

## 关键设计

- **EETWriter(parser)**: 接收已有解析器，遍历 .//ESP 节点
- **匹配**: 按 EDID 匹配 `collection.get(edid)`，校验 `context == "GRUP:CHAMP"`
- **写入**: 更新 `<TRADUIT>` 和 `<STATUS>` 节点文本
- **write(path)**: 将修改后的 XML 树写回文件

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/eet_xml_writer.py` | EETWriter |
