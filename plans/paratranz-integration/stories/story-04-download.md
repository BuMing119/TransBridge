# Story 04: 文件下载工作流

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

从 ParaTranz 下载翻译文件并合并到本地 Collection。支持单文件和批量下载，自动检测分割文件。

## 关键设计

- **匹配规则**: ParaTranz `key` == 本地 `entry.id`
- **stage 过滤**: translation 的 stage < min_stage → 跳过，不覆盖高质量译文
- **分割文件合并**: 自动检测 `Plugin.json`、`Plugin_1.json`、`Plugin_2.json` 等分割文件 → 批量下载 → 合并到同一 Collection
- **file_id 定位**: `list_files_with_path()` 返回完整路径→file_id 映射；`find_file_by_name()` 按文件名查找

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/workflow/downloader.py` | ParaTranzDownloader |
| `src/transbridge/paratranz/api/paratranz_files_api.py` | list_files_with_path, find_file_by_name |
