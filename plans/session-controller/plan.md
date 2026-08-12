# SessionController — 智能助手会话控制流提取

**对应需求**: FR12
**技术模块**: backend (smart_assistant)
**业务域**: AI 助手对话引擎
**状态**: ✅ 全部完成（2/2）
**创建日期**: 2026-08-05

## 功能边界

### 范围内
- 新建 `smart_assistant/session_controller.py`：显式状态机，5 状态 + 7 转换
- ChatWidget 瘦身：删除 `_run_llm_round()`、`_check_react_depth()`、`_check_react_continue()`、`_auto_execute_steps()`、`_react_depth` 属性等 ~150 行控制逻辑
- ConversationOrchestrator 移除内部分发：`_on_finished()` 不再做 Plan/Tool/Reply 模式分发，改为回调通知 Controller
- ToolExecutionHandler 移除 ReAct 触发：`_handle_result()` 不再末尾触发 `on_react_continue`，改为回调通知 Controller
- `__init__.py` 懒加载映射更新：新增 `SessionController` 符号
- TaskManager 异步任务完成回调接入 Controller

### 范围外
- 修改 ExecutionEngine / GraphExecutor（ADR-011 双层状态互不穿透）
- 修改 UI 外观 / 布局 / 样式
- 修改 LLM prompt / 工具功能 / 工具注册
- 修改 TaskManager / MemoryStore / GuardMiddleware / MCP
- 修改 ADR-011 图编排引擎
- 引入外部状态机库

## Story 清单

### Story 01: SessionController 核心实现 + 新旧并行

**归属**: session-controller（新建）

**验收标准**:
- [ ] `SessionController` 类实现完整状态机：`State` enum（IDLE/THINKING/AWAITING_CONFIRM/EXECUTING/AWAITING_TASK）+ 7 个 `handle_*` 转换方法
- [ ] 每个 `handle_*` 方法入口断言当前状态 + 出口调用 `_transition_to()`
- [ ] 6 个输入接口全部实现：`handle_user_message()` / `handle_user_confirmed()` / `handle_user_cancelled()` / `handle_execution_complete()` / `handle_task_completed()` / `handle_abort()`
- [ ] 5 个输出回调接口定义并注入：`on_state_changed` / `on_present_plan_card` / `on_present_tool_card` / `on_present_batch_tool_card` / `on_system_message` / `on_conversation_end`
- [ ] ReAct 深度管理内置（`_react_depth` 计数器 + `_MAX_REACT_DEPTH = 10`）
- [ ] `auto_mode` 属性支持读写
- [ ] ChatWidget 创建 Controller 实例，注入 Orchestrator/ToolHandler 及所有回调
- [ ] 新旧路径并行运行：Controller 执行状态转换的同时，ChatWidget 旧控制逻辑保留但加日志标记
- [ ] 161 现有测试零回归
- [ ] `__init__.py` 新增 `SessionController` 懒加载映射

> 详细实现指南见 `plans/session-controller/stories/story-01-core-and-parallel.md`（由 `/bm-story` 展开后生成）

### Story 02: 旧控制逻辑清理 + ChatWidget 瘦身

**归属**: session-controller（新建）

**验收标准**:
- [ ] ChatWidget 删除以下方法/属性：`_run_llm_round()`、`_check_react_depth()`、`_check_react_continue()`、`_auto_execute_steps()`、`_react_depth`（property getter+setter）
- [ ] ChatWidget 中所有 `_check_react_depth() + _run_llm_round()` 调用点替换为 Controller 对应方法
- [ ] ChatWidget 中 `_on_plan_confirmed()` 简化为调用 `controller.handle_user_confirmed()`
- [ ] ChatWidget 中 `_on_plan_all_finished()` 简化为调用 `controller.handle_execution_complete()`
- [ ] ChatWidget 中 `_on_task_completed()` / `_on_task_failed()` 简化为调用 `controller.handle_task_completed()`
- [ ] ChatWidget 中 `send_user_message()` 简化为调用 `controller.handle_user_message()`
- [ ] ConversationOrchestrator 新增 `on_response_parsed` 回调属性；`_on_finished()` 移除 Plan/Tool/Reply 分发逻辑
- [ ] ToolExecutionHandler 新增 `on_step_completed` 回调属性；`_handle_result()` 移除 `on_react_continue` 触发
- [ ] ChatWidget 瘦身后目标行数 ~600 行（从 1108）
- [ ] 所有被删除方法/属性的调用方已迁移，无残留引用
- [ ] 161+ 新测试全通过

> 详细实现指南见 `plans/session-controller/stories/story-02-cleanup.md`（由 `/bm-story` 展开后生成）

## 架构依赖

- **ADR-008** (更新: 2026-08-05): D8 顶层调度者 / D9 enum 分发表 / D10 双层状态 / D11 回调契约 / D12 两 Story 迁移
- **ADR-011**: GraphExecutor 不受影响，执行级状态管理保留原样
- **ADR-012**: 护栏链在 ToolExecutionHandler 中保持不变，SessionController 不介入

## 风险与回退方案

| 风险 | 缓解 | 回退 |
|------|------|------|
| S01 新旧并行产生行为不一致 | 日志比对 + 20-30 轮对话验证后才进入 S02 | 删除 Controller 实例化代码，恢复纯旧路径 |
| Orchestrator/ToolHandler 回调变更影响其他调用方 | 全量搜索 `on_react_continue` 和 `_on_finished` 的引用方 | 保留旧回调作为 deprecated wrapper，新回调并行存在 |
| 测试因 import 变更失败 | S01 阶段不删除任何旧方法，仅新增 | `git revert` 单文件回退 |
