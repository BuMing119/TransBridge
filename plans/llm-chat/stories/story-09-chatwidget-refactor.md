# Story 09: ChatWidget 拆分重构

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/`
**业务域**: AI 辅助翻译 — 智能助手 UI
**状态**: 已确认
**创建日期**: 2026-05-14
**对应问题**: QA 报告 C1 — ChatWidget 1120 行超重，违反 ADR-008 代码分层

## 前置依赖

### 上游 Story
- Story-08（同 plan）：FR7.16 文档流重构已完成，ChatWidget 当前结构稳定

### 引用的架构决策
- ADR-008: SmartAssistant 代码分层 — UI 组件与业务逻辑分离，UI 层只负责渲染和用户交互

## 概述

`ChatWidget` 当前 1120 行，承载 10+ 职责：UI 初始化（4 阶段）、消息管理、LLM 循环编排（ReAct/Plan/Auto）、工具执行、护栏构建、记忆检索、文件上传、滚动管理、思考指示器、Worker 生命周期。严重违反 SRP 和 ADR-008。

目标：按职责拆分为 3 个类，ChatWidget 降为纯 UI 层 ~400 行。

### 目标架构

```
ChatWidget (纯 UI, ~400 行, ui/tools/smart_assistant/)
  ├── 消息管理: add_*_bubble / add_system_message / add_tool_card / add_plan_card
  ├── 布局/滚动: _init_ui / _on_scroll_changed / _on_back_to_bottom
  ├── 输入/发送: _on_send / _input / _auto_mode / QShortcut
  ├── ThinkingIndicator: _show/_hide/_toggle_thought
  └── 委托: → ConversationOrchestrator / → ToolExecutionHandler

ConversationOrchestrator (~300 行, ui/tools/smart_assistant/)
  ├── LLM 轮次: _run_llm_round (A→B→C 微阶段)
  ├── 模式分发: _on_llm_finished → ReAct / Plan / Auto
  ├── 流式管理: _on_llm_chunk / _flush_streaming
  ├── Worker 生命周期: 创建 / cancel / 回调绑定
  └── 记忆/上下文: MemoryRetriever / ContextBuilder / 系统提示词

ToolExecutionHandler (~200 行, ui/tools/smart_assistant/)
  ├── 工具查找: ToolRegistry.get()
  ├── 权限检查: _needs_confirm (护栏中间件)
  ├── 执行编排: 单步 / 批量 / 重试 (RetryHandler)
  ├── 结果处理: _handle_tool_result → 反馈给编排器
  └── 护栏构建: _ensure_middlewares()
```

---

## 子 Story 清单

### Story-09-1: 提取 ToolExecutionHandler

**Phase**: 9.1 | **预估**: 2h | **依赖**: 无

**验收标准**:
- [ ] `ui/tools/smart_assistant/tool_execution_handler.py` 新建，`ToolExecutionHandler` 类
- [ ] `_on_tool_executed` 方法完整移入（~58行），拆分为 3 个子方法：`_check_permission` / `_execute_with_retry` / `_handle_result`
- [ ] `_needs_confirm` 方法移入（护栏权限检查）
- [ ] `_ensure_middlewares` 方法移入（护栏链延迟构建）
- [ ] `_handle_tool_result` 方法移入
- [ ] `_auto_execute_steps` 方法移入（自动模式批量执行）
- [ ] `_check_react_depth` 保留在 ChatWidget（纯 UI 状态）
- [ ] 回调注入模式：ToolExecutionHandler 通过回调 `on_result(callback)` 返回执行结果，不持有 ChatWidget 引用
- [ ] ChatWidget 中 import 更新，原有方法调用改为 `self._tool_handler.xxx()`
- [ ] 启动应用无 ImportError，工具执行功能正常

**实现步骤**:
1. 创建 `tool_execution_handler.py`，定义 `ToolExecutionHandler` 类 → `tool_execution_handler.py`（新建）
   - 接收参数：`ctx`, `on_add_system_message(callable)`, `on_add_tool_card(callable)`, `on_add_plan_card(callable)`
   - 从 chat_widget.py 搬迁方法：`_on_tool_executed`, `_needs_confirm`, `_ensure_middlewares`, `_handle_tool_result`, `_auto_execute_steps`
2. 拆分 `_on_tool_executed` → `_check_permission(step, middlewares) -> bool` + `_execute_with_retry(spec, step, middlewares, exec_ctx)` + `execute_step(step, middlewares)`
3. 在 ChatWidget 中初始化 `self._tool_handler = ToolExecutionHandler(...)`，原有方法调用改为 `self._tool_handler.xxx()` → `chat_widget.py`
4. 清理 ChatWidget 中已搬迁的方法定义 → `chat_widget.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/tool_execution_handler.py` | **新建** | ToolExecutionHandler 类（~200 行） |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 搬迁 5 个方法，初始化委托 |

---

### Story-09-2: 提取 ConversationOrchestrator

**Phase**: 9.2 | **预估**: 3h | **依赖**: Story-09-1（ToolExecutionHandler 已提取）

**验收标准**:
- [ ] `ui/tools/smart_assistant/conversation_orchestrator.py` 新建，`ConversationOrchestrator` 类
- [ ] `_run_llm_round` 完整移入（微阶段 A→B→C，~60 行）
- [ ] `_on_llm_finished` 完整移入（~60 行，拆分为模式分发子方法）
- [ ] `_on_llm_chunk` / `_flush_streaming` 移入
- [ ] `_get_llm_client` / `_get_prompt_builder` 移入
- [ ] `_on_llm_error` / `_on_retry` 移入
- [ ] Worker 生命周期管理移入：`_start_worker` / `_cleanup_worker`
- [ ] Memory/Context 管理移入：`_inject_memory_context` / `_build_context`
- [ ] `_SignalBridge` 内部类移入（仅编排器需要跨线程回调）
- [ ] 回调注入模式：编排器通过回调 `on_chunk(callable)`, `on_finished(callable)`, `on_error(callable)`, `on_streaming_bubble_created(callable)` 通知 UI
- [ ] ChatWidget 中 import 更新，`self._orchestrator = ConversationOrchestrator(...)`
- [ ] 启动应用无 ImportError，LLM 对话功能正常

**实现步骤**:
1. 创建 `conversation_orchestrator.py`，定义 `ConversationOrchestrator` 类 → `conversation_orchestrator.py`（新建）
   - 接收参数：`ctx`, `conversation_manager`, `tool_handler`, 回调 callbacks
   - 从 chat_widget.py 搬迁 `_SignalBridge` + 所有 LLM 相关方法
2. 拆分 `_on_llm_finished` → `_dispatch_mode(parsed)` + `_handle_react(parsed)` + `_handle_plan(parsed)` + `_handle_auto(parsed)`
3. 拆分 `_run_llm_round` → `_stage_a_prepare()` + `_stage_b_llm_call()` + `_stage_c_postprocess()`（保持微阶段拆分）
4. 在 ChatWidget 中初始化 `self._orchestrator = ConversationOrchestrator(...)` → `chat_widget.py`
5. 清理 ChatWidget 中已搬迁的方法和 `_SignalBridge` 内部类 → `chat_widget.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/conversation_orchestrator.py` | **新建** | ConversationOrchestrator 类（~300 行） |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 搬迁 LLM 编排方法，初始化委托 |

---

### Story-09-3: 精简 ChatWidget 为纯 UI

**Phase**: 9.3 | **预估**: 1.5h | **依赖**: Story-09-2（编排器已提取）

**验收标准**:
- [ ] ChatWidget 降为 ~400 行（从 1120 行）
- [ ] ChatWidget 仅保留：UI 构建（`_init_ui` 4 阶段）、消息管理（add_*_bubble）、滚动/回到底部、输入/发送（`_on_send`）、ThinkingIndicator、QuickActions、文件上传 UI
- [ ] `_on_send` 简化为：收集输入 → 调 `self._orchestrator.start_round(text)` → 清空输入框
- [ ] 所有业务逻辑通过回调接收结果，ChatWidget 不直接操作 Worker/Engine
- [ ] `panel.py` 适配：仅通过 ChatWidget 公开接口交互，不再访问 `_worker`/`_engine` 私有属性
- [ ] 无未使用的 import
- [ ] `__init__.py` 如有需要同步更新导出
- [ ] 启动应用无 ImportError，所有现有功能正常

**实现步骤**:
1. 清理 chat_widget.py → 移除所有已搬迁的 LLM/工具方法定义，保留纯 UI 方法 → `chat_widget.py`
2. 精简 `_on_send` → 委托 `self._orchestrator.start_round(text)` → `chat_widget.py`
3. 适配 `panel.py` → 移除对 `_worker`/`_engine` 私有属性的直接访问 → `panel.py`
4. 最终检查：import 清理、方法计数、行数验证 → `chat_widget.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 清理搬迁残留，精简 ~400 行 |
| `ui/tools/smart_assistant/panel.py` | 修改 | 适配新接口 |
| `ui/tools/smart_assistant/__init__.py` | 可能修改 | 如有导出变更 |

---

## 文件变更总清单

| 文件 | Story | 操作 | 说明 |
|------|-------|------|------|
| `ui/tools/smart_assistant/tool_execution_handler.py` | 09-1 | **新建** | ToolExecutionHandler 类 |
| `ui/tools/smart_assistant/conversation_orchestrator.py` | 09-2 | **新建** | ConversationOrchestrator 类 |
| `ui/tools/smart_assistant/chat_widget.py` | 09-1/2/3 | 修改 | 搬迁+精简，1120→~400 行 |
| `ui/tools/smart_assistant/panel.py` | 09-3 | 修改 | 适配新接口 |

## 回调契约

```
ChatWidget (UI)  ←──回调──  ConversationOrchestrator (编排)  ──调用──→  ToolExecutionHandler (工具)
     │                         │                                            │
     │  on_chunk(text)         │  tool_handler.execute_step(step)           │
     │  on_finished(parsed)    │  tool_handler.auto_execute(steps)          │
     │  on_error(msg, cat)     │                                            │
     │  on_streaming_start()   │  ←──回调──                                │
     │                         │  on_system_message(text, level)            │
     │                         │  on_tool_card(card)                        │
     │                         │  on_plan_card(card)                        │
```

**规则**：
- 编排器和工具处理器**不持有** ChatWidget 引用
- 所有 UI 更新通过回调函数传递
- 回调在 `_SignalBridge` 中自动排队到主线程（仅编排器持有桥接器）

## 风险与回退方案

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| 方法搬迁后隐式依赖断裂（如 `self._streaming_text` 同时被编排器和 UI 访问） | 流式渲染中断 | 搬迁前逐方法检查 `self.xxx` 属性访问，建立共享状态显式传递清单 |
| 回调链路过长导致调试困难 | 增加排查时间 | 保持回调命名与原有方法名一致，编排器中记录关键状态转换日志 |
| 微阶段拆分（QTimer.singleShot）在跨类后时序错乱 | LLM 轮次卡死 | 保持微阶段链在编排器内部，不跨类边界 |

## 风险与注意事项

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| 方法搬迁后隐式依赖断裂 | 功能异常 | git revert 回退，逐方法重新搬迁 |
| 回调链路过长 | 调试困难 | 保持回调命名与原有方法名一致 |
| 微阶段拆分跨类后时序错乱 | LLM 轮次卡死 | 微阶段链保持在编排器内部，不跨类边界 |
| `_SignalBridge` 搬迁后信号连接遗漏 | 跨线程回调失效 | 检查所有 `_cb_bridge._dispatch.connect` 调用点 |
