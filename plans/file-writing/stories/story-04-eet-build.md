# Story 04: EET XML 新建构建

**所属方案**: `plans/file-writing/plan.md`
**状态**: ✔️ 已实现

## 概述

从 Collection 构建全新的 EET XML 文件。用于没有已有 XML 模板的场景。

## 关键设计

- **EETBuilder.build(collection, output)**: 静态方法，从零构建 XML
- **XML 结构**: 创建 `<DocumentElement>` 根节点 → 遍历 Collection → 生成 `<ESP>` 子节点
- **字段映射**: id.split(":") → editor + formid；context.split(":") → GRUP + CHAMP

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/writer/eet_xml_builder.py` | EETBuilder |
