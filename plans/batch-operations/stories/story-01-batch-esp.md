# Story 01: 批量 ESP 解析

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

支持一次选择多个 ESP 文件进行批量解析，每个文件创建独立的 CollectionSlot。

## 关键设计

- **_browse_esp()**: QFileDialog.getOpenFileNames 多文件选择
- **独立 slot**: 每个 ESP 解析为独立 CollectionSlot（key=文件全路径）
- **自动激活**: 最后一个解析完成的 slot 自动激活

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/step1.py` | _browse_esp() 多文件处理 |
| `src/transbridge/ui/context.py` | add_slot() + activate_slot() |
