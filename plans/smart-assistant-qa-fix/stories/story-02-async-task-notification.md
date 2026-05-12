# Story 02: 异步任务完成通知

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: `smart_assistant/tools/`（task_manager + tool_translator）、`ui/tools/smart_assistant/`（chat_widget）
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: B2（异步翻译/润色任务完成后无通知机制）

## 前置依赖

### 上游 Story
- **Story-01**（安全护栏修复）: 已完成 → 提供 `execute_with_guardrails` 的 `middlewares` 参数接口，本 Story 不依赖

### 引用的架构决策
- **ADR-004**（QThread + 信号总线异步模式）: pyqtSignal 是项目标准异步通信机制
- **ADR-012 §2**（可观测性）: ObservabilityCollector 订阅信号管道，TaskManager 信号可复用于遥测

## 验收标准

- [ ] `TaskManager` 新增 `task_completed(task_id, result)` 和 `task_failed(task_id, error)` 两个 pyqtSignal
- [ ] `start_translation` / `start_polish` 后台线程完成时发射对应信号
- [ ] `ChatWidget` 连接信号，收到完成通知后将结果以 observation 消息追加到对话
- [ ] LLM 可通过 `get_task_status` 查询进度，也可通过系统通知得知完成
- [ ] 系统提示词告知 LLM 异步任务完成后会自动通知

## 数据流

```
用户请求翻译 → LLM 调用 start_translation
  │
  ▼
_tool_start_translation(args, ctx)
  ├── 检查参数合法 + collection 非空
  ├── 创建 stop_event + TaskManager.register()
  ├── 创建后台线程 _run()
  ├── ChatWidget 连接 TaskManager 信号（首次时）
  └── 返回 ToolResult.ok("翻译任务已启动", data={"task_id": task_id})

                          ║  后台线程运行中...
                          ║  LLM 可通过 get_task_status(task_id) 查询进度
                          ║

后台线程完成
  ├── tm.set_status(task_id, "completed")
  ├── tm.update_progress(task_id, result_stats)
  └── tm.task_completed.emit(task_id, result)        ← 新增 pyqtSignal
        │
        ▼
chat_widget.on_task_completed(task_id, result)       ← 新增 slot
  ├── _conversation.add_observation(
  │       f"start_translation",
  │       f"翻译任务 {task_id} 完成: 成功{result.success_count}, 失败{result.failed_count}"
  │   )
  └── _run_llm_round()                                ← 触发 LLM 继续推理
        │
        ▼
LLM 收到 observation → 分析结果 → 下一步（后处理/写回）

失败路径:
后台线程异常
  ├── tm.set_status(task_id, "failed")
  └── tm.task_failed.emit(task_id, str(exc))          ← 新增 pyqtSignal
        │
        ▼
chat_widget.on_task_failed(task_id, error)
  ├── _conversation.add_observation("start_translation", f"任务失败: {error}")
  └── _run_llm_round()
```

## 关键接口

### TaskManager 改为 QObject + 添加信号

```python
# src/transbridge/smart_assistant/tools/task_manager.py

from PyQt6.QtCore import QObject, pyqtSignal

class TaskManager(QObject):
    """长运行任务生命周期管理器（单例 + pyqtSignal 通知）。

    信号:
        task_completed(task_id, result_dict)  — 任务成功完成
        task_failed(task_id, error_message)   — 任务失败/取消
    """

    task_completed = pyqtSignal(str, dict)   # task_id, {status, success_count, ...}
    task_failed = pyqtSignal(str, str)       # task_id, error_message

    _instance: "TaskManager | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    # QObject.__init__ 由 super().__new__ 自动调用
                    instance._lock = threading.Lock()
                    instance._tasks: dict[str, TaskHandle] = {}
                    cls._instance = instance
        return cls._instance

    def notify_completed(self, task_id: str, result: dict) -> None:
        """线程安全的完成通知。可在任何线程调用。"""
        self.task_completed.emit(task_id, result)

    def notify_failed(self, task_id: str, error: str) -> None:
        """线程安全的失败通知。可在任何线程调用。"""
        self.task_failed.emit(task_id, error)
```

### ChatWidget 连接信号

```python
# src/transbridge/ui/tools/smart_assistant/chat_widget.py

class ChatWidget(QWidget):
    def __init__(self, ctx, ...):
        ...
        self._task_signals_connected = False

    def _ensure_task_signals(self) -> None:
        """首次调用时连接 TaskManager 信号。"""
        if self._task_signals_connected:
            return
        tm = TaskManager()
        tm.task_completed.connect(self._on_task_completed)
        tm.task_failed.connect(self._on_task_failed)
        self._task_signals_connected = True

    def _on_task_completed(self, task_id: str, result: dict) -> None:
        """后台任务完成回调。"""
        succ = result.get("success_count", 0)
        fail = result.get("failed_count", 0)
        skip = result.get("skipped_count", 0)
        msg = f"任务 {task_id} 完成: 成功 {succ}, 失败 {fail}"
        if skip:
            msg += f", 跳过 {skip}"
        self._conversation.add_observation("start_translation", msg)
        self.add_system_message(f"[OK] {msg}")
        if self._check_react_depth():
            self._run_llm_round()

    def _on_task_failed(self, task_id: str, error: str) -> None:
        """后台任务失败回调。"""
        msg = f"任务 {task_id} 失败: {error}"
        self._conversation.add_observation("start_translation", msg)
        self.add_system_message(f"[FAIL] {msg}")
        if self._check_react_depth():
            self._run_llm_round()
```

### 工具函数中发信号

```python
# src/transbridge/smart_assistant/tools/tool_translator.py

def _tool_start_translation(args: dict, ctx) -> ToolResult:
    ...
    def _run():
        try:
            ...
            result = translator.translate(...)
            tm.update_progress(task_id, {...})
            tm.set_status(task_id, "completed")
            # B2 FIX: 通知完成
            tm.notify_completed(task_id, {
                "status": "completed",
                "success_count": result.success_count,
                "failed_count": result.failed_count,
                "skipped_count": result.skipped_count,
            })
        except InterruptedError:
            tm.set_status(task_id, "cancelled")
            tm.notify_failed(task_id, "任务已被用户停止")
        except Exception as exc:
            tm.set_status(task_id, "failed")
            tm.update_progress(task_id, {"error": str(exc)})
            tm.notify_failed(task_id, str(exc))

    thread = threading.Thread(target=_run, daemon=True)
    ...
    # B2 FIX: ChatWidget 需要连接信号 ← 由调用方（chat_widget）负责
```

## 实现步骤

### 步骤 1: TaskManager 升级为 QObject + 添加信号

**涉及文件**: `src/transbridge/smart_assistant/tools/task_manager.py`（修改）

**实现要点**:
- `TaskManager` 继承 `QObject`（在 `__new__` 中调用 `super().__new__(cls)`）
- 添加 `task_completed = pyqtSignal(str, dict)`
- 添加 `task_failed = pyqtSignal(str, str)`
- 添加 `notify_completed(task_id, result)` 和 `notify_failed(task_id, error)` 方法

**边界条件**:
- QObject 与单例模式兼容 → `__new__` 中 `super().__new__(cls)` 自动调用 `QObject.__init__`
- 信号可从任何线程 emit → pyqtSignal 是线程安全的（Qt 自动跨线程排队）
- 第一次使用前 TaskManager 实例化 → 惰性单例，首次 `TaskManager()` 创建

**测试策略**:
- 连接信号到 mock slot → emit → 验证 slot 收到正确参数
- 多线程同时 emit → 无竞态

---

### 步骤 2: 后台线程完成时发射信号

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（修改）

**实现要点**:
- `_tool_start_translation` 的 `_run()` 中：成功完成时调用 `tm.notify_completed()`
- `_tool_start_polish` 的 `_run()` 中：同样添加完成通知
- 异常/取消路径添加 `tm.notify_failed()`

**边界条件**:
- `InterruptedError` → `notify_failed(task_id, "任务已被用户停止")`
- 其他 Exception → `notify_failed(task_id, str(exc))`
- `notify_completed` 和 `set_status("completed")` 同时调用，保持状态一致

**测试策略**:
- Mock translator 成功完成 → 验证 `notify_completed` 被调用
- Mock translator 抛异常 → 验证 `notify_failed` 被调用

---

### 步骤 3: ChatWidget 连接信号 + 更新提示词

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）、`src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- 添加 `_ensure_task_signals()` 方法
- 在 `_on_send()` 首次发送消息时调用（或 `__init__` 中延迟连接）
- `_on_task_completed` / `_on_task_failed` 追加 observation 并触发 LLM
- 更新系统提示词

**边界条件**:
- 同一 TaskManager 信号只连接一次 → `_task_signals_connected` 标志
- ChatWidget 关闭 → Qt 自动断开连接（QObject 父子关系）
- 多轮对话中任务完成 → 追加到当前对话，不影响历史
- 观察消息不应触发新的自动执行循环 → `_check_react_depth` 控制

**伪代码**:
```python
def _on_task_completed(self, task_id, result):
    msg = self._format_task_result(task_id, result)
    self._conversation.add_observation("start_translation", msg)
    self.add_system_message(f"[OK] {msg}")
    if self._check_react_depth():
        self._run_llm_round()
```

**测试策略**:
- emit `task_completed` → 验证 observation 追加 + `_run_llm_round` 被调用
- ReAct depth 耗尽时 emit → 验证不触发 `_run_llm_round`

### 步骤 4: 系统提示词更新

**涉及文件**: `src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- 在系统提示词的"可用工具"段中添加说明：
  "注意: `start_translation` 和 `start_polish` 是异步工具。调用后任务在后台执行，完成后会自动通知你结果。你也可以通过 `get_task_status` 随时查询进度。"

**测试策略**:
- 验证构建的 prompt 包含上述文字

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/task_manager.py` | 修改 | 改为 QObject + 添加 pyqtSignal + notify_* 方法 |
| `src/transbridge/smart_assistant/tools/tool_translator.py` | 修改 | `_run()` 中添加完成/失败通知 |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | 新增 signal 连接 + slot 方法 |
| `src/transbridge/smart_assistant/prompts.py` | 修改 | 补充异步通知说明 |

## 风险与注意事项

- **风险**: QObject 单例的 `__new__` 中 `super().__new__(cls)` 可能触发 QObject 初始化两次（Python 的 `__new__` + `__init__`）→ **缓解**: TaskManager 当前使用 `__new__` 单例，不定义 `__init__`，所有初始化在 `__new__` 的 `if cls._instance is None` 块内
- **风险**: pyqtSignal 在非 GUI 线程 emit → Qt 自动排队到接收者线程，无需额外处理
- **注意**: `notify_completed` 的 `result: dict` 应只包含可序列化的简单类型（str/int/list），避免传递 `TranslationResult` 等复杂对象
- **注意**: 批量翻译中可能同时有多个翻译任务 → 各自独立 task_id，ChatWidget 需处理并发通知（Qt 信号队列天然串行化）
