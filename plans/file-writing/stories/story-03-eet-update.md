# Story 03: ESP-ESM Translator XML 更新写入

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

更新已有 ESP-ESM Translator XML 文件中的翻译文本。基于已有 EET_XmlParser 实例，匹配并修改 TRADUIT 节点。

二进制 EET v3 原格式写回是独立 adapter，使用完整来源快照、`eet-v1` 物理身份、译文数字缓存同步和全文件发布验证；不复用 XML 节点定位。当前实验性接入与 QA 见 [EET 集成计划](../../eet-integration/plan.md)及 [ADR-053](../../../docs/adr/053-eet-binary-io-integration.md)。

## 关键设计

- **EETWriter(parser)**: 接收已有解析器，遍历 .//ESP 节点
- **匹配**: 按 EDID 匹配 `collection.get(edid)`，校验 `context == "GRUP:CHAMP"`
- **写入**: 更新 `<TRADUIT>` 和 `<STATUS>` 节点文本
- **write(path)**: 将修改后的 XML 树写回文件

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/eet_xml_writer.py` | EETWriter |
