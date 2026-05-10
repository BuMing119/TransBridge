# Story 13: API 配置对话框 + 项目列表面板

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

API Token 配置和项目列表管理。支持"全部项目"和"我参与的"双视图。

## 关键设计

- **ConfigDialog**: API Token + base_url 输入对话框，保存到 INI
- **ProjectListPanel**: 全部项目（无过滤）/ 我参与的（?uid=xxx API 参数）双视图
- **NewProjectDialog**: 新建项目对话框（项目名 + 描述）
- **401 触发**: _http_error_bus.http_error(401) → 自动弹出 ConfigDialog

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/paratranz/config_dialog.py` | ConfigDialog |
| `src/transbridge/ui/paratranz/project_panel.py` | ProjectListPanel + NewProjectDialog |
