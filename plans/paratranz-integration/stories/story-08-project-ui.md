# Story 08: 项目管理 UI

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

ParaTranz 项目管理面板的多标签页界面，覆盖项目全生命周期管理。

## 关键设计

- **多标签页**: 概览/文件/词条/术语/成员/历史/贡献/导出/讨论（9 个 Tab）
- **ProjectListPanel**: 全部项目 + 我参与的（uid 过滤）双视图
- **ConfigDialog**: API Token 配置对话框
- **成员管理**: 权限感知（issueMode 检查：内部模式非成员禁用、私密模式非管理员禁用）
- **Generation Counter**: 异步请求防串位（11 个组件统一模式）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/paratranz/widget.py` | ParaTranzWidget 主面板 |
| `src/transbridge/ui/paratranz/project_panel.py` | ProjectListPanel + NewProjectDialog |
| `src/transbridge/ui/paratranz/config_dialog.py` | ConfigDialog |
| `src/transbridge/ui/paratranz/{overview,files,strings,terms,members,history,contribution,export,issues}_tab.py` | 各功能 Tab |
