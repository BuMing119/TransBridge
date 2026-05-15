# 001: 方案策划 — Story-09 ChatWidget 拆分重构

**日期**: 2026-05-14
**类型**: 增
**关联**: Epic: 智能助手侧边栏面板 > Story 09: ChatWidget 拆分重构

## 修改文件

### `plans/llm-chat/stories/story-09-chatwidget-refactor.md` (增)
- **修改内容**: 新建详细 Story 文档（~250 行）。定义 3 子 Story：09-1 提取 ToolExecutionHandler（工具权限/执行/重试/护栏）、09-2 提取 ConversationOrchestrator（LLM轮次/模式分发/流式/Worker）、09-3 精简 ChatWidget 为纯 UI。包含目标架构图、回调契约定义、`_SignalBridge` 搬迁方案、微阶段（A→B→C）保持策略
- **原因**: QA 报告 C1 — ChatWidget 1120 行/48 方法，违反 ADR-008 代码分层原则，需拆分重构

### `plans/llm-chat/plan.md` (改)
- **修改内容**: 状态从"已实现"更新为"已确认（Story-01~08 已实现，Story-09 待编码）"；在 Story-08 后追加 Story-09 节（Phase 9, 预估 6.5h, 3 子 Story, 文件变更清单）
- **原因**: 方案策划阶段产出，追加新 Story 到已有 Epic plan

### `plans/INDEX.md` (改)
- **修改内容**: llm-chat 行 Story 数 8→9，状态更新为"已实现 (S01-08) + 待编码 (S09)"，追加 s09 文档链接
- **原因**: 同步 Story 计数与状态
