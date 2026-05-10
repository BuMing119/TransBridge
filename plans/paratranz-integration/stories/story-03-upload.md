# Story 03: 文件上传工作流

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

将 Collection 按分类导出并上传至 ParaTranz。支持多种上传模式、冲突检测和分类文件选择。

## 关键设计

- **两阶段流程**: 预检冲突（单次 API 调用获取文件映射）→ 上传
- **上传模式**: none（仅更新原文）/ safe（导入译文不覆盖人工编辑）/ force（强制覆盖）
- **分类上传**: 按 context 分类拆分 → 弹出文件选择对话框 → 用户勾选 → 逐文件上传
- **冲突检测**: detect_conflicts() → _ConflictResolveDialog 交互式选择目标
- **路径映射**: path_mapping 参数支持指定上传目录路径
- **FileMaps**: existing/path_based/name_to_files — 两阶段流程传递映射数据

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/workflow/uploader.py` | ParaTranzUploader, UploadResult, ConflictInfo, FileMaps |
| `src/transbridge/paratranz/api/paratranz_files_api.py` | 文件 CRUD API |
