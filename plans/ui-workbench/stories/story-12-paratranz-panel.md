# Story 12: ParaTranz 管理面板

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

ParaTranz 平台的全部管理功能 UI 面板。9 个标签页覆盖项目全生命周期。

## 关键设计

- **ParaTranzWidget**: 主面板，持有所有 Tab
- **9 个 Tab**: 概览(overview) / 文件(files) / 词条(strings) / 术语(terms) / 成员(members) / 历史(history) / 贡献(contribution) / 导出(export) / 讨论(issues)
- **词条详情弹窗**: StringDetailDialog 独立弹窗（替代内嵌右侧面板），自定义绘制导航列表（原文+键名）
- **历史记录**: 支持词条 ID 筛选 + 行详情弹窗（左右分栏修改前后）
- **贡献统计**: 开始/结束日期选择器
- **讨论权限**: issueMode 字段（内部模式非成员禁用、私密模式非管理员禁用）
- **Generation Counter**: 所有异步 Tab 引入计数器防串位

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/paratranz/widget.py` | ParaTranzWidget |
| `src/transbridge/ui/paratranz/overview_tab.py` | 概览 |
| `src/transbridge/ui/paratranz/files_tab.py` | 文件管理 |
| `src/transbridge/ui/paratranz/strings_tab.py` | 词条管理 + StringDetailDialog |
| `src/transbridge/ui/paratranz/terms_tab.py` | 术语管理 |
| `src/transbridge/ui/paratranz/members_tab.py` | 成员管理 |
| `src/transbridge/ui/paratranz/history_tab.py` | 历史记录 |
| `src/transbridge/ui/paratranz/contribution_tab.py` | 贡献统计 |
| `src/transbridge/ui/paratranz/export_tab.py` | 导出管理 |
| `src/transbridge/ui/paratranz/issues_tab.py` | 讨论管理 |
