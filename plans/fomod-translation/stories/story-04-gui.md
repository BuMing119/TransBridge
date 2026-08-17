# Story 04: GUI 面板

**所属方案**: plans/fomod-translation/plan.md
**技术模块**: ui
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 03（pipeline）

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式
- ADR-008: UI 与后端分层（UI 仅调用 pipeline）

## 验收标准

- [ ] 新建 src/transbridge/ui/tools/fomod/ 向导式面板
- [ ] 4 步向导：选文件→审核变更→翻译→组装输出
- [ ] 后台执行复用 QThread（ApiWorker 模式）+ 进度反馈
- [ ] 结果摘要：迁移统计 + diff 摘要

## 实现步骤

### 步骤 1: 向导式面板骨架

涉及文件: src/transbridge/ui/tools/fomod/fomod_panel.py（新建）

实现要点:
- 4 步 QWizard 或 QDialog 分页：选文件→审核 diff→翻译进度→组装输出
- 复用现有 SmartAssistantPanel 的 QDockWidget 模式 或独立对话框

### 步骤 2: 后台执行 + 进度

实现要点:
- 用 QThread Worker 包装 FomodPipeline.run()
- 进度信号转发（解包/diff/翻译/打包各步进度）
- 完成后展示结果摘要

边界条件:
- 取消操作 → 中断后台线程
- 无旧版 → 向导跳过 diff 审核步

## 文件变更清单

src/transbridge/ui/tools/fomod/（新建面板）、main_window 入口挂载