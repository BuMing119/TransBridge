# Story 02: TaskManager — 长运行任务生命周期管理

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（按确认书 v2 更新：+线程安全强化 +pause_event预留 +线程跟踪）

## 前置依赖

### 上游 Story
- Story 01（同 plan）→ 提供 `tools/base.py` 的 `ToolResult` 数据类

### 引用的架构决策
- ADR-004: QThread 异步模式（TaskManager 不替代 QThread，仅管理任务元数据和停止信号）
- ADR-008: tools/ 子包归属业务逻辑层

## 验收标准

- [ ] `TaskManager` 单例类在 `tools/task_manager.py` 中实现（**模块级双重检查锁**防止竞态条件，E3）
- [ ] `TaskHandle` dataclass：`stop_event: threading.Event`, `pause_event: threading.Event | None`（**B5联动：预留字段不实现**）, `status: str`, `progress: dict`, `created_at: float`, `metadata: dict`
- [ ] `register(task_id, stop_event, metadata) -> str` — 注册新任务，返回 task_id
- [ ] `cancel(task_id) -> bool` — 设置 stop_event + 更新状态为 "cancelled"
- [ ] `get_status(task_id) -> dict` — 返回 progress dict 时**深拷贝**（E3: 防止并发遍历 RuntimeError）
- [ ] `list_active() -> list[str]` — 列出所有非终止状态的任务 ID
- [ ] `cleanup(task_id)` — 移除已完成/已取消的任务句柄，**确保线程 join**（O9: daemon=True 防止阻止进程退出）
- [ ] 跟踪所有线程引用，cleanup 时确保 join（O9）
- [ ] 线程安全（内部使用 `threading.Lock` + 模块级 `_instance_lock`）

## 数据流

```
start_translation 工具调用
    │
    ├─→ stop_event = threading.Event()
    ├─→ task_id = TaskManager.register(stop_event, metadata)
    │       └─ TaskHandle(stop_event, pause_event=None, status="running", ...) 存入 _tasks
    │
    ├─→ ThreadPoolExecutor.submit(翻译任务, stop_event)
    │       └─ 翻译循环定期检查 stop_event.is_set()
    │
    ├─→ 进度回调 → TaskManager._tasks[task_id].progress 更新
    │
    └─→ 完成/异常 → TaskManager._tasks[task_id].status = "completed"/"failed"

stop_task 工具调用
    └─→ TaskManager.cancel(task_id) → stop_event.set() → status = "cancelled"

stop_all_tasks 工具调用（E7: Story 06 实现）
    └─→ 遍历所有活跃任务 → TaskManager.cancel(each)

get_task_status 工具调用
    └─→ TaskManager.get_status(task_id) → 返回深拷贝的可序列化快照
```

## 关键接口

```python
# smart_assistant/tools/task_manager.py

import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

@dataclass
class TaskHandle:
    stop_event: threading.Event
    pause_event: threading.Event | None = None  # B5联动: 预留字段，P2真实暂停时实现
    status: str = "running"  # "running" | "completed" | "failed" | "cancelled"
    progress: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)
    _thread: threading.Thread | None = None  # O9: 线程引用跟踪


class TaskManager:
    """长运行任务生命周期管理器（单例）。
    
    线程安全：所有公开方法持有 _lock。模块级 _instance_lock 双重检查。
    """
    _instance: "TaskManager | None" = None
    _instance_lock: threading.Lock = threading.Lock()  # E3: 单例锁
    _lock: threading.Lock
    _tasks: dict[str, TaskHandle]

    def __new__(cls) -> "TaskManager":
        # E3: 双重检查锁防竞态
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._lock = threading.Lock()
                    cls._instance._tasks = {}
        return cls._instance

    def register(self, stop_event: threading.Event | None = None,
                 metadata: dict | None = None,
                 thread: threading.Thread | None = None) -> str:  # O9: thread参数
        """注册新任务，返回 task_id。"""

    def cancel(self, task_id: str) -> bool:
        """取消任务：set stop_event + 更新状态。返回是否成功。"""

    def get_status(self, task_id: str) -> dict:
        """获取任务状态快照。progress 深拷贝防止并发 RuntimeError。（E3）"""
        with self._lock:
            handle = self._tasks.get(task_id)
            if not handle:
                return {"error": "任务不存在"}
            return {
                "task_id": task_id,
                "status": handle.status,
                "progress": copy.deepcopy(handle.progress),  # E3: 深拷贝
                "created_at": handle.created_at,
                "metadata": dict(handle.metadata),
            }

    def update_progress(self, task_id: str, progress: dict) -> None:
        """更新任务进度信息。"""

    def list_active(self) -> list[str]:
        """列出所有状态为 running 的任务 ID。（E7联动: 不再有 paused 状态）"""

    def cleanup(self, task_id: str) -> None:
        """移除已完成/已取消的任务句柄。确保线程 join。（O9）"""
        with self._lock:
            handle = self._tasks.pop(task_id, None)
        if handle and handle._thread and handle._thread.is_alive():
            handle._thread.join(timeout=5)  # O9: 确保线程退出
```

## 实现步骤

### 步骤 1: 定义 `TaskHandle` 数据类 + `TaskManager` 骨架

**涉及文件**: `src/transbridge/smart_assistant/tools/task_manager.py`（新建）

**实现要点**:
- `TaskHandle` 为 dataclass，5 个字段
- `TaskManager` 单例模式（`__new__`），`_tasks: dict[str, TaskHandle]` + `_lock: threading.Lock`
- 所有公开方法用 `with self._lock:` 保护

**边界条件**:
- `TaskManager()` 多次调用返回同一实例
- `_tasks` 初始为空

---

### 步骤 2: 实现 `register()` / `cancel()` / `get_status()`

**涉及文件**: 同上追加

**实现要点**:
- `register()`: 若 `stop_event` 为 None 则自动创建，生成 UUID task_id，创建 TaskHandle 存入 _tasks
- `cancel()`: 若 task_id 存在且 status 非终止状态，调用 `handle.stop_event.set()` + 设 `status="cancelled"`，返回 True；否则返回 False
- `get_status()`: 返回 TaskHandle 的可序列化快照（dict），不含 stop_event 对象

**边界条件**:
- `cancel()` 对不存在的 task_id → 返回 False
- `get_status()` 对不存在的 task_id → 返回 `{"error": "task not found"}`
- `cancel()` 对已终止的任务 → 返回 False（幂等）

---

### 步骤 3: 实现 `update_progress()` / `list_active()` / `cleanup()`

**涉及文件**: 同上追加

**实现要点**:
- `update_progress()`: 合并 progress dict 到 handle.progress（浅合并）
- `list_active()`: 过滤 `status in ("running", "paused")`
- `cleanup()`: 删除 `status in ("completed", "failed", "cancelled")` 的任务

**边界条件**:
- `update_progress()` 对不存在的 task_id → 静默忽略
- `list_active()` 无活跃任务 → 返回空列表
- `cleanup()` 对不存在的 task_id → 静默忽略
- 内存泄漏防护：`cleanup()` 在 `get_status()` 检测到终止状态时自动调用

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/task_manager.py` | 新建 | TaskHandle + TaskManager 单例 |
| `smart_assistant/tools/__init__.py` | 修改 | 导出 TaskManager |

## 风险与注意事项

- **风险 1**: `stop_event` 被 cancel 后，实际翻译线程可能仍在运行（因为有检查间隔）→ 这是正常行为，cancel 是"请求停止"而非"强制杀死"
- **注意**: TaskHandle 不可跨进程共享（`threading.Event` 仅限同一进程内线程间通信）
- **注意**: `get_status()` 返回的 dict 必须可 JSON 序列化（用于 Checkpoint），metadata 中的复杂对象需由调用方保证可序列化
