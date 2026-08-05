"""TaskManager — 长运行任务生命周期管理器（单例 + 回调通知）。

跨线程安全: notify_completed / notify_failed 通过可注入的主线程调度器
将回调投递到主线程执行。默认使用直接调用（非 GUI 上下文），UI 层可注入
Qt 队列调度器以安全地在主线程操作 GUI（符合 ADR-008 后端去 PyQt6 要求）。
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

logger = logging.getLogger(__name__)

# ── 主线程调度器协议 ─────────────────────────────────────────
# 调度器签名: dispatcher(fn: Callable[[], None]) -> None
# - 默认实现直接调用 fn()（适用于非 GUI / 测试上下文）
# - UI 层通过 set_main_thread_dispatcher() 注入 Qt 队列调度器


def _default_dispatcher(fn: Callable[[], None]) -> None:
    """默认调度器：直接调用（非 GUI 上下文 / 测试用）。"""
    fn()


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
    """长运行任务生命周期管理器（单例 + 回调通知）。

    回调:
        on_completed(callback)  — 注册任务完成回调: fn(task_id, result_dict)
        on_failed(callback)     — 注册任务失败回调: fn(task_id, error_message)
        remove_listener(callback) — 移除已注册的回调

    线程安全：所有公开方法持有 _lock。模块级 _instance_lock 双重检查。(E3)

    Phase 1 解耦: 移除 QObject/pyqtSignal 继承，改用纯 Python 回调列表，
    消除 C++ 元对象系统的栈空间占用。
    """

    _instance: "TaskManager | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    # 主线程调度器：由 UI 层注入（符合 ADR-008 后端去 PyQt6 要求）
    _dispatcher: Callable[[Callable[[], None]], None] = _default_dispatcher

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = object.__new__(cls)
                    instance._lock = threading.Lock()
                    instance._tasks: dict[str, TaskHandle] = {}
                    instance._listeners: dict[str, list] = {"completed": [], "failed": [], "finished": []}
                    cls._instance = instance
        return cls._instance

    # ── 调度器注入 (ADR-008: 消除 PyQt6 耦合) ────────────────

    @classmethod
    def set_main_thread_dispatcher(cls,
                                   dispatcher: Callable[[Callable[[], None]], None],
                                   ) -> None:
        """注入主线程调度器。UI 层应在初始化时调用此方法。

        调度器签名: dispatcher(fn: Callable[[], None]) -> None
        fn 是零参数可调用对象，调度器负责将其投递到主线程执行。

        示例（Qt UI 层）:
            from PyQt6.QtCore import QMetaObject, Qt, QCoreApplication
            app = QCoreApplication.instance()
            TaskManager.set_main_thread_dispatcher(
                lambda fn: QMetaObject.invokeMethod(app, fn, Qt.QueuedConnection))
        """
        TaskManager._dispatcher = dispatcher

    @classmethod
    def reset_dispatcher(cls) -> None:
        """重置为默认调度器（直接调用，用于测试或 TUI 上下文）。"""
        TaskManager._dispatcher = _default_dispatcher

    # ── 回调注册 (Phase 1: 替代 pyqtSignal) ──────────────────────

    def on_finished(self, callback: Callable) -> None:
        """注册任务完成/失败统一回调。callback(task_id, success, message, data)。"""
        with self._lock:
            self._listeners["finished"].append(callback)

    def on_completed(self, callback) -> None:
        """[Deprecated] 注册任务完成回调。请使用 on_finished。

        callback(task_id, result_dict)
        """
        def _wrapper(task_id, success, message, data):
            if success:
                callback(task_id, data or {})
        self.on_finished(_wrapper)

    def on_failed(self, callback) -> None:
        """[Deprecated] 注册任务失败回调。请使用 on_finished。

        callback(task_id, error_message)
        """
        def _wrapper(task_id, success, message, data):
            if not success:
                callback(task_id, message)
        self.on_finished(_wrapper)

    def remove_listener(self, callback) -> None:
        """移除已注册的回调（从 completed、failed 和 finished 列表中都尝试移除）。"""
        with self._lock:
            for key in ("completed", "failed", "finished"):
                try:
                    self._listeners[key].remove(callback)
                except ValueError:
                    pass

    # ── 公开 API ──────────────────────────────────────────────

    def register(self, stop_event: threading.Event | None = None,
                 metadata: dict | None = None,
                 thread: threading.Thread | None = None) -> str:
        """注册新任务，返回 task_id。默认创建 stop_event 和 pause_event（初始 set）。"""
        task_id = uuid4().hex[:12]
        if stop_event is None:
            stop_event = threading.Event()
        handle = TaskHandle(
            stop_event=stop_event,
            pause_event=threading.Event(),  # Story 26: 默认创建，初始 set（非暂停）
            metadata=dict(metadata or {}),
            _thread=thread,
        )
        handle.pause_event.set()  # 初始非暂停状态
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
        # 若任务处于暂停状态，唤醒以便退出
        if handle.pause_event is not None:
            handle.pause_event.set()
        handle.status = "cancelled"
        return True

    def pause(self, task_id: str) -> bool:
        """暂停任务：clear pause_event，更新状态为 paused。返回是否成功。"""
        with self._lock:
            handle = self._tasks.get(task_id)
        if handle is None:
            return False
        if handle.pause_event is None:
            handle.pause_event = threading.Event()
        handle.pause_event.clear()
        handle.status = "paused"
        return True

    def resume(self, task_id: str) -> bool:
        """恢复任务：set pause_event，更新状态为 running。返回是否成功。"""
        with self._lock:
            handle = self._tasks.get(task_id)
        if handle is None:
            return False
        if handle.pause_event is None:
            handle.pause_event = threading.Event()
        handle.pause_event.set()
        handle.status = "running"
        return True

    def get_status(self, task_id: str) -> dict:
        """获取任务状态快照。

        m3: progress 是扁平 dict（键为 "current"/"total"/"error" 等），
        dict() 浅拷贝即可，无需昂贵的 deepcopy。属性读取全部在锁内进行。
        """
        with self._lock:
            handle = self._tasks.get(task_id)
            if handle is None:
                return {"error": "任务不存在", "task_id": task_id}
            return {
                "task_id": task_id,
                "status": handle.status,
                "progress": dict(handle.progress),  # m3: 浅拷贝（progress 是扁平 dict）
                "created_at": handle.created_at,
                "metadata": dict(handle.metadata),
            }

    def update_progress(self, task_id: str, progress: dict) -> None:
        """更新任务进度信息。m10: progress 修改在锁内。"""
        with self._lock:
            handle = self._tasks.get(task_id)
            if handle is not None:
                handle.progress.update(progress)

    def list_active(self) -> list[str]:
        """列出所有活跃任务 ID（含 running 和 paused 状态）。"""
        with self._lock:
            return [tid for tid, h in self._tasks.items() if h.status in ("running", "paused")]

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

    def start_thread(self, task_id: str, target: Callable[[], None]) -> threading.Thread:
        """M2: 创建并启动守护线程，关联到已注册任务。

        封装 tool_translator/tool_proofreader 中重复的线程创建样板：
            thread = threading.Thread(target=_run, daemon=True)
            handle = tm.get_handle(task_id)
            if handle:
                handle._thread = thread
            thread.start()

        get_handle() 已持有内部锁，此处无需额外加锁。

        Returns:
            创建的线程对象。
        """
        thread = threading.Thread(target=target, daemon=True)
        handle = self.get_handle(task_id)
        if handle is not None:
            handle._thread = thread
        thread.start()
        return thread

    # B2: 异步通知方法（线程安全，可在任何线程调用）

    def notify_finished(self, task_id: str, success: bool, message: str = "",
                        data: dict | None = None) -> None:
        """统一通知任务完成。按 success 分发到对应的回调列表。

        通过注入的调度器投递回调到主线程执行。
        调度器默认为直接调用（非 GUI 上下文），UI 层应通过
        set_main_thread_dispatcher() 注入 Qt 队列调度器以保证 GUI 操作安全。
        """
        # C3-fix: 在锁内快照监听器列表，锁外派发，避免回调执行时持有锁
        with self._lock:
            finished = list(self._listeners.get("finished", []))
            legacy = list(self._listeners.get(
                "completed" if success else "failed", []))
        dispatch = TaskManager._dispatcher
        for cb in finished:
            dispatch(lambda c=cb, t=task_id, s=success, m=message, d=data:
                     self._safe_callback(c, t, s, m, d))
        if success:
            for cb in legacy:
                dispatch(lambda c=cb, t=task_id, d=data or {}:
                         self._safe_callback(c, t, d))
        else:
            for cb in legacy:
                dispatch(lambda c=cb, t=task_id, m=message:
                         self._safe_callback(c, t, m))

    def notify_completed(self, task_id: str, result: dict) -> None:
        """[Deprecated] 通知任务成功完成。请使用 notify_finished。"""
        self.notify_finished(task_id, True, "", result)

    def notify_failed(self, task_id: str, error: str) -> None:
        """[Deprecated] 通知任务失败/取消。请使用 notify_finished。"""
        self.notify_finished(task_id, False, error)

    @staticmethod
    def _safe_callback(cb, *args) -> None:
        """在回调外层包装 try/except，隔离单回调异常。"""
        try:
            cb(*args)
        except Exception as exc:
            logger.warning("TaskManager 回调异常: %s", exc)

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
        """清理所有非活跃任务，返回清理数量。

        m4: 锁内收集句柄后立即释放锁，再在锁外 join 线程，
        避免 thread.join(timeout=2) 阻塞其他 TaskManager 操作。
        """
        with self._lock:
            inactive = [tid for tid in list(self._tasks.keys())
                        if self._tasks[tid].status in ("completed", "failed", "cancelled")]
            handles_to_clean = [self._tasks.pop(tid) for tid in inactive]
        # 锁外 join，避免阻塞其他 TaskManager 操作
        for handle in handles_to_clean:
            if handle._thread is not None and handle._thread.is_alive():
                handle._thread.join(timeout=2)
        return len(inactive)

    @classmethod
    def reset(cls) -> None:
        """m18: 会话切换时重置单例状态。清理所有任务、线程和回调。"""
        with cls._instance_lock:
            if cls._instance is not None:
                with cls._instance._lock:
                    for tid in list(cls._instance._tasks.keys()):
                        handle = cls._instance._tasks.pop(tid)
                        handle.stop_event.set()
                        # 仅 join 已启动的线程（ident 为 None 表示未启动）
                        if handle._thread is not None and handle._thread.ident is not None:
                            handle._thread.join(timeout=2)
                # Phase 1: 清理回调列表，防止悬空引用
                cls._instance._listeners["completed"].clear()
                cls._instance._listeners["failed"].clear()
                cls._instance._listeners["finished"].clear()
                # ADR-008: 重置调度器为默认直接调用（非 GUI 上下文）
                TaskManager._dispatcher = _default_dispatcher
                cls._instance = None
