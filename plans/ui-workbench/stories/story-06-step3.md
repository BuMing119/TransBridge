# Story 06: Step3 操作面板

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

工作台第三步：操作执行面板。承载 UploadCard/DownloadCard/WriteCard 三张操作卡片。

## 关键设计

- **卡片布局**: 垂直排列三张 OpCard
- **项目感知**: _on_project_changed → 启用/禁用 ParaTranz 相关卡片
- **集合感知**: _on_collection_changed → 更新各卡片状态

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/step3.py` | Step3OpsWidget |
