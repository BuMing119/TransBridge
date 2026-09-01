"""Wait for assistant tool jobs without mistaking admission for completion."""

from __future__ import annotations

import threading

from transbridge.application.tasks import TaskEventFilter


class GraphTaskAwaiter:
    def __init__(self, manager, cancelled: threading.Event, paused: threading.Event) -> None:
        self._manager = manager
        self._cancelled = cancelled
        self._paused = paused
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def control(self, action: str) -> None:
        with self._lock:
            tasks = tuple(self._active)
        for task_id in tasks:
            getattr(self._manager, action)(task_id)

    def resolve(self, result):
        data = result.get("data")
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if not result.get("success", True) or not isinstance(task_id, str) or not task_id:
            return result
        handle = self._manager.get_handle(task_id)
        if handle is None or handle.execution is None:
            return {"success": False, "message": f"无法等待后台任务: {task_id}", "data": data}
        changed = threading.Event()
        subscription = self._manager.runtime.subscribe(
            lambda _event: changed.set(), event_filter=TaskEventFilter(run_id=handle.execution.ref.run_id)
        )
        with self._lock:
            self._active.add(task_id)
        try:
            if self._cancelled.is_set():
                self._manager.cancel(task_id)
            elif self._paused.is_set():
                self._manager.pause(task_id)
            while True:
                changed.clear()
                status = self._manager.get_status(task_id)
                state = status.get("status")
                if state in {"completed", "failed", "cancelled"} and handle.execution.join(task_id, 0):
                    terminal_data = dict(data)
                    terminal_data.update(status.get("result") or {})
                    terminal_data["status"] = state
                    return {
                        "success": state == "completed",
                        "message": status.get("message")
                        or {"completed": "任务完成", "failed": "任务失败", "cancelled": "任务已取消"}[state],
                        "data": terminal_data,
                    }
                if "error" in status:
                    return {"success": False, "message": status["error"], "data": data}
                changed.wait(0.05)
        finally:
            with self._lock:
                self._active.discard(task_id)
            subscription.close()
