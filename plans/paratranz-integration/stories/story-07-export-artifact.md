# Story 07: 导出工件工作流

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

触发 ParaTranz 导出并下载导出结果（zip 文件）。用于获取项目的完整翻译文件存档。

## 关键设计

- **ArtifactWorkflow**: 触发导出 + 轮询等待 + 下载 zip
- **轮询机制**: 记录最新 artifact 的 createdAt (t0) → trigger_export() → 循环 get_artifacts() → 等待 createdAt > t0 → download
- **下载**: 下载 artifact zip 文件到指定本地目录

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/workflow/artifact.py` | ArtifactWorkflow |
| `src/transbridge/paratranz/api/paratranz_export_api.py` | ParatranzExportAPI |
