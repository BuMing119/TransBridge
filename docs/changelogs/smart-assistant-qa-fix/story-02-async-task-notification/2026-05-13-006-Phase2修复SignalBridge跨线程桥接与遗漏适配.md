# 006: Phase 2 修复 — _SignalBridge 跨线程桥接与遗漏适配

**日期**: 2026-05-13
**类型**: 改
**关联**: Epic: Smart Assistant QA 修复 > Story 02: 异步任务完成通知

## 背景

Phase 2 将 ChatWorker/AgentWorker 改为 AsyncWorker(threading.Thread) 后，chat_widget.py 中使用了两种跨线程桥接方式——`QTimer.singleShot(0, lambda)` 和 `QMetaObject.invokeMethod(app, callable, QueuedConnection)`——均在此 PyQt6 版本中不工作：
- `QTimer.singleShot` 在无事件循环的 worker 线程中永远不触发
- `QMetaObject.invokeMethod` 不接受 callable 参数（仅接受字符串方法名）

导致流式文本卡在 "..."、Token 统计不更新、消息永远不完成。

## 修改文件

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**:
  - 新增 `_SignalBridge(QObject)` 类：1 个 `pyqtSignal(object)`，worker 线程通过 `_dispatch.emit(callback)` 将回调排队到主线程
  - `__init__`: 创建 `self._cb_bridge = _SignalBridge()` 并连接 `_dispatch` 到 `lambda cb: cb()`
  - `_run_llm_round_stage_c`: 4 个回调从 `QMetaObject.invokeMethod` 改为 `_bridge._dispatch.emit(lambda: ...)`，利用 pyqtSignal 原生的跨线程 QueuedConnection 自动排队
  - `_init_ui_stage1`: ObservabilityCollector 回调从 `QMetaObject.invokeMethod` 改为直接调用 `lambda stats: self._on_token_stats_updated(stats)`——因为 `on_llm_tokens` 已通过 _SignalBridge 在主线程执行
  - 导入：新增 `QObject`，移除 `QMetaObject`/`QCoreApplication`
- **原因**: `pyqtSignal.emit()` 在非主线程调用时 Qt 自动使用 QueuedConnection 排队到接收者线程。`_SignalBridge` 用 1 个 QObject + 1 个 signal 实现所有 worker→主线程回调的安全桥接，等价于原 ChatWorker QThread 的信号机制

### `src/transbridge/ui/main_window.py` (改)
- **修改内容**: L174 `isRunning()` → `is_alive()`；L176 `wait(3000)` → `join(timeout=3)`
- **原因**: Phase 2 将 ChatWorker 从 QThread 改为 threading.Thread 后，`main_window.py` closeEvent 中遗漏的 QThread 特定方法调用导致 `AttributeError`
