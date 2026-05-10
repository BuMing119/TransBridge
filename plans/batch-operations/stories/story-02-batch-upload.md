# Story 02: 批量上传

**所属方案**: `plans/batch-operations/plan.md`
**状态**: ✔️ 已实现

## 概述

批量上传多个已加载集合到 ParaTranz。支持插件选择、模式选择、滚动确认/结果。

## 关键设计

- **_SlotSelectDialog**: 列出所有已加载 slot → 用户勾选
- **_BatchUploadModeDialog**: 选择上传模式（none/safe/force），批量统一应用
- **_BatchConfirmDialog**: 滚动展示所有待上传项（插件名 → 文件列表）→ 用户确认
- **_BatchResultDialog**: 滚动展示上传结果（created/updated/skipped/failed）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/cards/upload_card.py` | 批量上传流程 + 各对话框 |
