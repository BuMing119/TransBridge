# 004: Story-09-3 编码 — ChatWidget 精简为纯 UI

**日期**: 2026-05-14
**类型**: 改
**关联**: Epic: 智能助手侧边栏面板 > Story 09: ChatWidget 拆分重构 > 09-3: 精简 ChatWidget 为纯 UI

## 修改文件

### `ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: 803→800 行。移除 3 个未使用 import（`pyqtSignal`/`QObject`/`ChatWorker`）；移除 `self._middlewares` 死状态（已内化到 ToolExecutionHandler）；移除 `_STREAMING_FLUSH_MS` 死常量（流式 timer 已移入编排器）。ChatWidget 最终 800 行，均为纯 UI 职责（4 阶段初始化、消息气泡管理、滚动、输入框、思考指示器、计划/工具卡片、系统消息、自动模式切换、文件上传、快捷操作、清空对话、观测切换、回到底部按钮、事件过滤），不含任何业务逻辑
- **原因**: Story-09-3 收尾 — 清理搬迁残留，验证 ADR-008 UI/逻辑分离完整性
