# ADR-004: PyQt6 QThread + 信号总线异步模式

- **状态**: 已接受
- **日期**: 2026-01 (回顾性记录于 2026-05-06)
- **决策者**: BuMing

## Context

TransBridge 是一个 PyQt6 桌面应用，需要执行大量耗时操作（文件解析、API 请求、LLM 调用、批量翻译），同时必须保持 UI 响应。需要选择异步执行模型。

## Decision

采用 **QThread + 信号总线** 模式，而非 asyncio 或线程池：

```python
class ApiWorker(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def run(self):
        # 在后台线程执行耗时操作
        result = self._fn(*self._args, **self._kwargs)
        self.result.emit(result)
```

**关键约定**:
- `ApiWorker` 是唯一后台执行通道，所有 API 请求和耗时操作必须通过此类
- 401/403 HTTP 错误通过全局 `_http_error_bus` 集中处理，不触发 `error` 信号
- API 状态通过 `_api_status_bus` 广播（状态栏指示器：绿点/转圈/红点）
- Worker 引用必须保留在 `self._workers` 列表中，防止被 GC 回收
- 暂停/停止控制使用 `threading.Event` + 自定义 `BaseException`（`_CancelledByPause`/`_CancelledByStop`），穿透 `except Exception` 捕获层

## Consequences

- **正**: 与 PyQt6 事件循环天然兼容，无 asyncio 冲突
- **正**: 信号/槽机制提供类型安全的线程间通信
- **正**: 集中式错误处理（401/403），避免各组件重复弹窗
- **正**: BaseException 控制流确保暂停/停止信号能穿透所有异常捕获
- **负**: QThread 创建/销毁有开销
- **负**: 无法利用 asyncio 生态的库（如 httpx async client）
- **负**: 并发控制需要手动管理（ThreadPoolExecutor 包装在 QThread 内）

## Alternatives Considered

- **asyncio + qasync**: 用 asyncio 替代 QThread → 拒绝：与现有 openai/anthropic 同步 SDK 集成困难，LLM 流式响应的取消逻辑需要重写
- **QThreadPool + QRunnable**: 轻量级线程池 → 部分采用（用于并发批次），但顶层控制仍用 QThread
- **multiprocessing**: 多进程 → 拒绝：数据共享复杂（Collection 在进程间传递开销大）
