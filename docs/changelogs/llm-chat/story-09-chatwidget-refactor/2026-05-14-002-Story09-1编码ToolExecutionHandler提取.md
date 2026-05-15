# 002: Story-09-1 编码 — ToolExecutionHandler 提取

**日期**: 2026-05-14
**类型**: 增/改
**关联**: Epic: 智能助手侧边栏面板 > Story 09: ChatWidget 拆分重构 > 09-1: 提取 ToolExecutionHandler

## 修改文件

### `ui/tools/smart_assistant/tool_execution_handler.py` (增)
- **修改内容**: 新建 `ToolExecutionHandler` 类（192 行），回调注入模式，不持有 ChatWidget 引用。搬迁 5 个方法：`_ensure_middlewares`（护栏链延迟构建）、`execute_step`（原 `_on_tool_executed`，单步工具执行+权限检查+重试循环）、`_handle_result`（原 `_handle_tool_result`，结果格式化+ReAct继续）、`auto_execute_steps`（原 `_auto_execute_steps`，自动模式批量执行）、`_needs_confirm`（提取为静态方法）。回调接口：`on_system_message`/`on_plan_card`/`on_tool_card`/`on_batch_tool_card`/`on_plan_confirmed`/`on_react_continue`
- **原因**: Story-09-1 方案 — 按 ADR-008 将工具执行逻辑从 ChatWidget UI 层分离

### `ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: 新增 `import ToolExecutionHandler`；`_init_ui_stage1` 中初始化 `self._tool_handler = ToolExecutionHandler(...)` 并注入 6 个回调；5 个方法重写为委托：`_ensure_middlewares`（3行）、`_on_tool_executed`（3行）、`_auto_execute_steps`（2行）→ 委托给 `self._tool_handler.xxx()`；`_handle_tool_result`（15行）完全移除（已内部化）；新增 `_check_react_continue`（3行，封装 `_check_react_depth` + `_run_llm_round` 供回调使用）。总计 1120→1002 行（-118 行）
- **原因**: 配合 ToolExecutionHandler 提取，ChatWidget 中对应方法改为委托调用
