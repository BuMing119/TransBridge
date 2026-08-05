# 003: Story 01 编码 — SessionController 核心实现 + 新旧并行

**日期**: 2026-08-05
**类型**: 增/改
**关联**: Epic: SessionController 会话控制流提取 > Story 01: SessionController 核心实现 + 新旧并行

## 修改文件

### `src/transbridge/smart_assistant/session_controller.py` (增)
- **修改内容**: 新建 SessionController 类（~230行）。实现 5 状态枚举（IDLE/THINKING/AWAITING_CONFIRM/EXECUTING/AWAITING_TASK）+ 8 个 handle_* 输入接口（handle_user_message/handle_llm_response/handle_user_confirmed/handle_user_cancelled/handle_execution_complete/handle_task_completed/handle_task_started/handle_abort）+ 6 个输出回调（on_state_changed/on_present_plan_card/on_present_tool_card/on_present_batch_tool_card/on_system_message/on_conversation_end）+ 2 个 UI 操作回调（on_llm_round_start/on_thinking_indicator_hide）。内置 ReAct 深度管理（_MAX_REACT_DEPTH=10）+ auto_mode 属性。状态转换使用 enum + assert + _transition_to() 显式分发表（ADR-008 D9）。_any_needs_confirm 委托给 ToolHandler。
- **原因**: 将当前分散在 ChatWidget/ConversationOrchestrator/ToolExecutionHandler 中的会话主循环控制流提取为显式状态机。遵循 ADR-008 D8（顶层调度者）+ D10（双层状态，不穿透 GraphExecutor）。

### `src/transbridge/smart_assistant/__init__.py` (改)
- **修改内容**: `__all__` 列表新增 `"SessionController"`；`_SYMBOL_MODULES` 字典新增 `"SessionController": ".session_controller"` 懒加载映射
- **原因**: 遵循 ADR-008 惰性加载模式，避免 Windows 1MB C 栈溢出。

### `src/transbridge/smart_assistant/conversation_orchestrator.py` (改)
- **修改内容**: `__init__` 新增 `on_response_parsed: Callable[[dict], None] | None = None` 参数 + `self._on_response_parsed` 存储；`_on_finished()` 在 `parsed = pb.parse_hybrid_response(response)` 和 `self._conversation.add_assistant(response)` 之后立即调用 `self._on_response_parsed(parsed)`
- **原因**: FR12 新增回调契约（ADR-008 D11），使 SessionController 能在 LLM 响应到达时收到通知并执行状态转换（THINKING→AWAITING/EXECUTING/IDLE）。

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: (1) 新增 `from ...session_controller import SessionController` 导入；(2) `_init_ui_stage1()` 中在 Orchestrator 创建之后新增 SessionController 实例化（~30行），注入 orchestrator/tool_handler/conversation 及全部回调；(3) 7 处关键方法新增 Controller 并行调用：
  - `_do_send_retrieve_and_run()` → `controller.handle_user_message(text)`（IDLE→THINKING）
  - `_on_tool_executed()` → `controller.handle_user_confirmed([step], "react")`
  - `_on_batch_executed()` → `controller.handle_user_confirmed()` + `handle_execution_complete()`
  - `_on_batch_ignored()` → `controller.handle_user_cancelled()`
  - `_on_tool_ignored()` → `controller.handle_user_cancelled()`
  - `_on_plan_confirmed()` → `controller.handle_user_confirmed(steps, "plan")`
  - `_on_plan_all_finished()` → `controller.handle_execution_complete(results)`
  - `_on_task_completed()` → `controller.handle_task_completed(task_id, result)`
  - `_on_task_failed()` → `controller.handle_task_completed(task_id, {"error": ...})`
  - `_check_react_continue()` → `controller.handle_execution_complete([])`
  - Orchestrator 构造新增 `on_response_parsed=lambda parsed: controller.handle_llm_response(parsed)`
  所有旧控制逻辑完整保留（新旧并行，ADR-008 D12）。
- **原因**: 将 SessionController 嵌入 ChatWidget 的创建和回调链路中，使其作为影子观察者运行，同步追踪状态转换但不替代旧代码路径。

### `tests/smart_assistant/test_session_controller.py` (增)
- **修改内容**: 新建测试文件（200行，35 测试用例）。覆盖 3 个测试类：TestSessionControllerInit（4 测试：初始状态/深度/auto_mode）、TestStateTransitions（23 测试：全部 8 个 handle_* 方法的状态转换+断言+回调）、TestStateChangeCallback（2 测试：单次/多次转换回调）、TestNeedsConfirmDelegation（2 测试：无 handler 默认行为/委托验证）
- **原因**: 确保状态机所有转换路径可测试、可回归。覆盖正常路径和断言失败路径。
