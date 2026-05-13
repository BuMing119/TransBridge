# 005: Phase 2 ObservabilityCollector + ChatWorker/AgentWorker 去 QObject/QThread

**日期**: 2026-05-13
**类型**: 增/改
**关联**: Epic: Smart Assistant QA 修复 > Story 02: 异步任务完成通知

## 背景

Phase 1 已完成 TaskManager + MemoryWriterThread 去 QObject。Phase 2 继续消除剩余后端 QObject 耦合（ObservabilityCollector、ChatWorker、AgentWorker），将后端 QObject 类从 6 个削减到 1 个（仅剩 ExecutionEngine）。

## 修改文件

### `src/transbridge/smart_assistant/workers/async_worker.py` (增)
- **修改内容**: 新建 `AsyncWorker(threading.Thread)` 基类。提供 `_cancelled` (threading.Event)、`cancel()`、`is_cancelled()` 以及 4 个回调属性（`on_chunk`/`on_finished`/`on_error`/`on_token_usage`）。替代 QThread + pyqtSignal 模式
- **原因**: ChatWorker 和 AgentWorker 有显著重复的取消/回调模式，提取基类消除重复。回调由调用方通过 QTimer.singleShot 桥接到主线程保证 Qt GUI 安全

### `src/transbridge/smart_assistant/workers/__init__.py` (增)
- **修改内容**: 子包导出 `AsyncWorker`
- **原因**: 模块化组织

### `src/transbridge/smart_assistant/observability/collector.py` (改)
- **修改内容**: 移除 `QObject`/`pyqtSignal` 继承；`__init__` 新增 `on_token_stats_updated: Callable | None = None` 回调参数；`on_llm_tokens()` 中 `self.token_stats_updated.emit(...)` 改为 `if self._on_token_stats_updated: self._on_token_stats_updated(...)`；移除 `parent` 参数；新增 `from typing import Callable`
- **原因**: 1 个 pyqtSignal 换取整个 QObject 继承性价比低。回调由 chat_widget 在构造函数注入，通过 QMetaObject.invokeMethod 确保跨线程安全

### `src/transbridge/smart_assistant/chat_worker.py` (改)
- **修改内容**: 移除 `QThread`/`pyqtSignal` 导入；`ChatWorker(QThread)` → `ChatWorker(AsyncWorker)`；移除 4 个 pyqtSignal 类属性；`__init__` 移除 `self._cancelled = threading.Event()`（由基类提供）；`run()` 中 `.emit()` 调用改为 `if self.on_xxx: self.on_xxx(...)` 回调检查；`cancel()` 调用 `super().cancel()` 设置取消标志后仍尝试取消 HTTP 客户端
- **原因**: QThread 的 C++ 元对象开销换为纯 Python threading.Thread。4 个 pyqtSignal 改为回调属性，由调用方通过 QTimer.singleShot 桥接到主线程

### `src/transbridge/smart_assistant/agents/agent_worker.py` (改)
- **修改内容**: 移除 `QThread`/`pyqtSignal` 导入；`AgentWorker(QThread)` → `AgentWorker(AsyncWorker)`；移除 3 个 pyqtSignal；新增 3 个回调属性 `on_progress`/`on_finished`/`on_error`；`__init__` 移除 `parent` 参数（threading.Thread 不支持）；`run()` 中 `.emit()` 改为 `if self.on_xxx: self.on_xxx(...)`；`_cancelled` bool 改为基类的 `is_cancelled()` 方法
- **原因**: 与 ChatWorker 保持一致的解耦模式。AgentWorker 当前未被任何代码实例化，修改零回归风险

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**:
  - 模块导入新增 `QMetaObject`、`QCoreApplication`
  - `_init_ui_stage1`: `ObservabilityCollector` 构造传入 `on_token_stats_updated` 回调（通过 QMetaObject.invokeMethod 投递主线程），替代 `signal.connect()`
  - `_run_llm_round_stage_c`: `ChatWorker` 回调赋值替代 `signal.connect()`，4 个回调均通过 `QTimer.singleShot(0, ...)` 桥接到主线程
  - `_on_llm_finished` / `_on_llm_error`: 信号断开（`disconnect` + `deleteLater`）改为回调清 None
  - `_on_retry` / `_on_send` / `_clear_conversation`: `isRunning()` → `is_alive()`，`wait(ms)` → `join(timeout=s)`
- **原因**: 适配 ObservabilityCollector 和 ChatWorker 的 QObject→回调迁移。所有回调在 worker 线程中调用，通过 QTimer.singleShot 确保 Qt 组件操作在主线程执行

### `src/transbridge/ui/tools/smart_assistant/panel.py` (改)
- **修改内容**: `closeEvent`: `token_stats_updated.disconnect(...)` → `_on_token_stats_updated = None`；`isRunning()` → `is_alive()`；`wait(3000)` → `join(timeout=3)`
- **原因**: 适配所有 QObject 信号→回调 + QThread→Thread 的 API 变更
