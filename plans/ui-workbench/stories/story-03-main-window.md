# Story 03: 主窗口框架

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

QMainWindow 主窗口，双 Tab 布局（工作台 + ParaTranz 管理）+ 菜单栏 + 快捷键 + 状态栏。

## 关键设计

- **双 Tab**: CentralWidget = QTabWidget（工作台 / ParaTranz 管理）
- **MenuBar**: 小工具菜单（AI翻译入口等）+ 视图菜单（面板可见性管理）
- **状态栏**: _ApiStatusIndicator（API 状态指示器）+ 用户标签
- **信号连接**: AppContext 信号 → _on_config_changed/_on_user_changed/_on_project_selected/_on_collection_changed
- **navigate_to**: 支持跨 Tab 导航

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/main_window.py` | MainWindow |
| `src/transbridge/ui/app.py` | QApplication 初始化入口 |
