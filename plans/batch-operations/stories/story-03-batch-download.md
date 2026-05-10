# Story 03: 批量下载

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

批量从 ParaTranz 下载翻译文件并合并到本地集合。自动检测分割文件。

## 关键设计

- **_find_split_files()**: 检测 `Plugin.json`、`Plugin_1.json`、`Plugin_2.json` 等命名模式
- **分割文件合并**: 下载所有匹配文件 → 合并到同一 Collection
- **结果详情**: 显示合并的文件名列表

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/cards/download_card.py` | _find_split_files + 批量下载 |
