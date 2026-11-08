# Story 04: ESP-ESM Translator XML 新建构建

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

从 Collection 构建全新的 ESP-ESM Translator XML 文件。用于没有已有 XML 模板的场景。

此新建合同仅适用于 XML。二进制 EET 的[正式集成](../../eet-integration/plan.md)只允许完整可信来源上的原格式写回，不支持插件→EET 或从零构建缺少原生字段的完整 EET。

## 关键设计

- **EETBuilder.build(collection, output)**: 静态方法，从零构建 XML
- **XML 结构**: 创建 `<DocumentElement>` 根节点 → 遍历 Collection → 生成 `<ESP>` 子节点
- **字段映射**: id.split(":") → editor + formid；context.split(":") → GRUP + CHAMP

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/eet_xml_builder.py` | EETBuilder |
