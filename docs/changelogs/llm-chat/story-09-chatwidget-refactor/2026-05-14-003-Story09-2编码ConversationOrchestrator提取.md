# 003: Story-09-2 编码 — ConversationOrchestrator 提取

**日期**: 2026-05-14
**类型**: 增/改
**关联**: Epic: 智能助手侧边栏面板 > Story 09: ChatWidget 拆分重构 > 09-2: 提取 ConversationOrchestrator

## 修改文件

### `ui/tools/smart_assistant/conversation_orchestrator.py` (增)
- **修改内容**: 新建 `ConversationOrchestrator` 类（362 行）。内嵌 `_SignalBridge(QObject+pyqtSignal)` 跨线程回调桥接器。搬迁 10 个方法/职责：`start_round`（Stage A 微阶段，原 `_run_llm_round`）、`_stage_b`（创建流式气泡）、`_stage_c`（ChatWorker 启动+回调绑定）、`_on_chunk`（流式累积+节流标记）、`_flush_streaming`（节流刷新）、`_on_finished`（响应解析+模式分发+记忆记录+Worker清理）、`_on_error`（错误分类+重试触发）、`retry`（重试编排）、`cancel_current_round`（中断清理）、`reset_state`（状态重置）。17 个回调注入参数覆盖所有 UI 交互
- **原因**: Story-09-2 方案 — 将 LLM 编排逻辑从 ChatWidget UI 层分离，遵循 ADR-008

### `ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: 1002→803 行（-199 行）。移除 `_SignalBridge` 内部类；移除 `__init__` 中 11 个搬迁到编排器的状态属性（`_worker/_react_depth/_prompt_builder/_consecutive_errors/_llm_client/_cb_bridge/_round_messages/_streaming_text/_streaming_bubble/_streaming_dirty/_streaming_timer`）；新增 `_react_depth` property（委托给 `_orchestrator.react_depth`）；`_init_ui_stage1` 中新增 `ConversationOrchestrator` 初始化（注入 17 个回调）；6 个方法重写为委托：`_get_prompt_builder`/`_get_llm_client`/`_run_llm_round`/`_on_retry`；4 个方法移除（`_run_llm_round_stage_b/_c/_on_llm_chunk/_flush_streaming/_on_llm_finished/_on_llm_error`）；新增 4 个辅助方法：`_do_streaming_flush`/`_remove_widget_safely`/`_offer_retry_button`/`_log_conversation_memory`；`_on_send` 简化为 `_orchestrator.cancel_current_round()`；`_clear_conversation` 简化为 `_orchestrator.cancel_current_round()+reset_state()`；`_on_auto_mode_toggled` 追加 `_orchestrator.auto_mode` 同步
- **原因**: 配合 ConversationOrchestrator 提取，ChatWidget 降为纯 UI 委托层

### `ui/tools/smart_assistant/panel.py` (改)
- **修改内容**: `closeEvent` 中 `self._chat._worker` → `self._chat._orchestrator.worker`
- **原因**: Worker 已移入编排器，panel 需要通过编排器访问
