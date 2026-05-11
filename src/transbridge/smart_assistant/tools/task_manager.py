"""TaskManager — 长运行任务生命周期管理器（单例）。"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class TaskHandle:
    """单个任务的运行时句柄。"""
    stop_event: threading.Event
    pause_event: threading.Event | None = None  # B5联动: 预留字段，P2真实暂停时实现
    status: str = "running"  # "running" | "completed" | "failed" | "cancelled"
    progress: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)
    _thread: threading.Thread | None = None  # O9: 线程引用跟踪


class TaskManager:
    """长运行任务生命周期管理器（单例）。

    线程安全：所有公开方法持有 _lock。模块级 _instance_lock 双重检查。(E3)
    """

    _instance: "TaskManager | None" = None
    _instance_lock: threading.Lock = threading.Lock()  # E3: 单例锁

    def __new__(cls) -> "TaskManager":
        # E3: 双重检查锁防竞态
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._lock = threading.Lock()
                    instance._tasks: dict[str, TaskHandle] = {}
                    cls._instance = instance
        return cls._instance

    # ── 公开 API ──────────────────────────────────────────────

    def register(self, stop_event: threading.Event | None = None,
                 metadata: dict | None = None,
                 thread: threading.Thread | None = None) -> str:
        """注册新任务，返回 task_id。"""
        task_id = uuid4().hex[:12]
        if stop_event is None:
            stop_event = threading.Event()
        handle = TaskHandle(
            stop_event=stop_event,
            metadata=dict(metadata or {}),
            _thread=thread,
        )
        with self._lock:
            self._tasks[task_id] = handle
        return task_id

    def cancel(self, task_id: str) -> bool:
        """取消任务：set stop_event + 更新状态。返回是否成功。"""
        with self._lock:
            handle = self._tasks.get(task_id)
        if handle is None:
            return False
        handle.stop_event.set()
        handle.status = "cancelled"
        return True

    def get_status(self, task_id: str) -> dict:
        """获取任务状态快照。progress 深拷贝防止并发 RuntimeError。(E3)"""
        with self._lock:
            handle = self._tasks.get(task_id)
        if handle is None:
            return {"error": "任务不存在", "task_id": task_id}
        return {
            "task_id": task_id,
            "status": handle.status,
            "progress": copy.deepcopy(handle.progress),  # E3: 深拷贝
            "created_at": handle.created_at,
            "metadata": dict(handle.metadata),
        }

    def update_progress(self, task_id: str, progress: dict) -> None:
        """更新任务进度信息。"""
        with self._lock:
            handle = self._tasks.get(task_id)
        if handle is not None:
            handle.progress.update(progress)

    def list_active(self) -> list[str]:
        """列出所有状态为 running 的任务 ID。(E7联动: 不再有 paused 状态)"""
        with self._lock:
            return [tid for tid, h in self._tasks.items() if h.status == "running"]

    def list_all(self) -> list[str]:
        """列出所有任务 ID（含已完成的）。"""
        with self._lock:
            return list(self._tasks.keys())

    def set_status(self, task_id: str, status: str) -> bool:
        """M5: 设置任务状态。外部不再直接访问 _lock/_tasks。"""
        with self._lock:
            handle = self._tasks.get(task_id)
            if handle is not None:
                handle.status = status
                return True
        return False

    def get_handle(self, task_id: str) -> TaskHandle | None:
        """M5: 安全获取任务句柄（含锁保护）。"""
        with self._lock:
            return self._tasks.get(task_id)

    def cleanup(self, task_id: str) -> None:
        """移除已完成/已取消的任务句柄。确保线程 join。(O9)"""
        with self._lock:
            handle = self._tasks.pop(task_id, None)
        if handle is None:
            return
        # O9: 确保线程退出
        if handle._thread is not None and handle._thread.is_alive():
            handle._thread.join(timeout=5)

    def cleanup_all(self) -> int:
        """清理所有非活跃任务，返回清理数量。"""
        with self._lock:
            inactive = [tid for tid, h in self._tasks.items()
                        if h.status in ("completed", "failed", "cancelled")]
            for tid in inactive:
                handle = self._tasks.pop(tid)
                if handle._thread is not None and handle._thread.is_alive():
                    handle._thread.join(timeout=2)
            return len(inactive)
