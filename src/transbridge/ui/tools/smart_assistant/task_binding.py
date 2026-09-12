from __future__ import annotations

from collections.abc import Callable
import logging
import re

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from transbridge.smart_assistant.tools.task_control import task_scope_matches

logger = logging.getLogger(__name__)


class _MainThreadDispatcher(QObject):
    requested = pyqtSignal(object)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.requested.connect(
            lambda callback: callback(),
            Qt.ConnectionType.QueuedConnection,
        )

    def dispatch(self, callback) -> None:
        self.requested.emit(callback)


def sanitize_error_message(message: str) -> str:
    if not message:
        return message
    message = re.sub(r'[A-Za-z]:[\\/][^\s,;:"]+', "[path]", message)
    message = re.sub(r'/(?:home|Users|usr|tmp|var|etc|opt|root|mnt)/[^\s,;:"]+', "[path]", message)
    message = re.sub(r"\b(sk-[A-Za-z0-9_-]{20,})\b", "[api_key]", message)
    return re.sub(r"\b(sk-ant-[A-Za-z0-9_-]{20,})\b", "[api_key]", message)


class TaskBinding:
    """Owns event-driven TaskManager listeners and coalesced UI refresh."""

    def __init__(
        self,
        *,
        parent,
        conversation,
        system_message: Callable[[str], None],
        controller: Callable[[], object | None],
        sanitize_error: Callable[[str], str],
        scope: Callable[[], dict] = dict,
        on_lifecycle_changed: Callable[[], None] | None = None,
    ) -> None:
        self._parent = parent
        self._dispatcher = _MainThreadDispatcher(parent)
        self._conversation = conversation
        self._system_message = system_message
        self._controller = controller
        self._sanitize_error = sanitize_error
        self._scope = scope
        self._on_lifecycle_changed = on_lifecycle_changed
        self._manager = None
        self._dispatcher_installed = False
        self._monitor = None
        self._refresh_timer: QTimer | None = None
        self._closed = False
        self._seen_terminals: set[tuple[str, str]] = set()
        self._settled_terminals: set[tuple[str, str]] = set()
        self._seen_states: dict[tuple[str, str], str] = {}

    def start(self) -> None:
        if self._closed or self._manager is not None:
            return
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        TaskManager.set_main_thread_dispatcher(self._dispatcher.dispatch)
        self._dispatcher_installed = True
        manager = TaskManager()
        manager.on_terminal(self._on_terminal)
        manager.on_updated(self._on_updated)
        self._manager = manager
        self.refresh()

    def set_monitor(self, monitor) -> None:
        if self._closed:
            return
        self._monitor = monitor
        self.refresh()

    def reset_monitor(self) -> None:
        if not self._closed and self._monitor is not None:
            self._monitor.reset()

    def refresh(self) -> None:
        if self._closed or self._monitor is None:
            return
        try:
            manager = self._manager
            if manager is None:
                from transbridge.smart_assistant.tools.task_manager import TaskManager

                manager = TaskManager()
            tasks = []
            for task_id in manager.list_all():
                status = manager.get_status(task_id)
                if not status.get("error") and task_scope_matches(status, self._scope()):
                    tasks.append(status)
            self._monitor.refresh(tasks)
        except Exception:
            logger.debug("刷新任务监控失败", exc_info=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer.deleteLater()
            self._refresh_timer = None
        if self._manager is not None:
            self._manager.remove_listener(self._on_terminal)
            self._manager.remove_listener(self._on_updated)
            self._manager = None
        if self._dispatcher_installed:
            from transbridge.smart_assistant.tools.task_manager import TaskManager

            TaskManager.reset_dispatcher()
            self._dispatcher_installed = False
        self._monitor = None
        self._seen_terminals.clear()
        self._settled_terminals.clear()
        self._seen_states.clear()

    def _on_updated(self, task_id: str) -> None:
        if self._closed:
            return
        if self._manager is not None:
            status = self._manager.get_status(task_id)
            if not status.get("error") and task_scope_matches(status, self._scope()):
                identity = (task_id, str(status.get("run_id", "")))
                state = status.get("status", "")
                previous = self._seen_states.get(identity)
                self._seen_states[identity] = state
                if state == "cancelling" and previous != state and self._on_lifecycle_changed is not None:
                    self._on_lifecycle_changed()
        if self._monitor is None:
            return
        if self._refresh_timer is None:
            self._refresh_timer = QTimer(self._parent)
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.setInterval(100)
            self._refresh_timer.timeout.connect(self.refresh)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _on_finished(self, task_id: str, success: bool, message: str, data: dict | None) -> None:
        """Compatibility entry point for direct delivery; queued delivery captures its snapshot."""
        if self._closed or self._manager is None:
            return
        self._on_terminal(self._manager.get_status(task_id), success, message, data)

    def reconcile_task(self, task_id: str) -> None:
        """Settle work that finished before the controller registered its wait."""
        if self._closed or self._manager is None:
            return
        status = self._manager.get_status(task_id)
        self._on_terminal(
            status,
            status.get("status") == "completed",
            str(status.get("message") or ""),
            status.get("result"),
        )

    def _on_terminal(self, status: dict, success: bool, message: str, data: dict | None) -> None:
        if self._closed or self._manager is None or status.get("error"):
            return
        if status.get("metadata", {}).get("assistant_request_id"):
            # Application request events own persistence and continuation. A view
            # must not inject this result into whichever question is foreground.
            self.refresh()
            return
        task_id = str(status.get("task_id", ""))
        run_id = str(status.get("run_id", ""))
        terminal = status.get("status")
        if not task_id or not run_id or terminal not in {"completed", "failed", "cancelled"}:
            return
        if (terminal == "completed") != success or not task_scope_matches(status, self._scope()):
            return
        current = self._manager.get_status(task_id)
        if not current.get("error") and (
            current.get("run_id") != run_id
            or current.get("status") != terminal
            or not task_scope_matches(current, self._scope())
        ):
            return
        identity = (task_id, run_id)
        controller = self._controller()
        accepted = controller is not None and controller.accepts_task_completion(task_id, run_id)
        awaited = accepted and controller.is_awaiting_task(task_id, run_id)
        already_notified = identity in self._seen_terminals
        if already_notified and (not awaited or identity in self._settled_terminals):
            return
        self._seen_terminals.add(identity)
        if success:
            result = data or {}
            succ = result.get("success_count", 0)
            fail = result.get("failed_count", 0)
            skip = result.get("skipped_count", 0)
            entry_count = result.get("entry_count", succ + fail + skip)
            parts = []
            if entry_count:
                parts.append(f"共 {entry_count} 条")
            if succ:
                parts.append(f"成功 {succ}")
            if fail:
                parts.append(f"失败 {fail}")
            if skip:
                parts.append(f"跳过 {skip}")
            detail = ", ".join(parts)
            text = f"任务 {task_id} 完成: {detail}" if detail else f"任务 {task_id} 完成"
            prefix = "[OK]"
        elif terminal == "cancelled":
            safe_message = self._sanitize_error(message)
            text = f"任务 {task_id} 已取消" + (f": {safe_message}" if safe_message else "")
            result = {"status": "cancelled", "cancelled": True, "message": safe_message}
            prefix = "[CANCELLED]"
        else:
            safe_error = self._sanitize_error(message)
            text = f"任务 {task_id} 失败: {safe_error}"
            result = {"status": "failed", "error": safe_error}
            prefix = "[FAIL]"
        if awaited:
            self._conversation.add_observation("start_translation", text)
        if not already_notified:
            self._system_message(f"{prefix} {text}")
        if accepted:
            controller.handle_task_completed(task_id, result, run_id)
            self._settled_terminals.add(identity)
        if self._on_lifecycle_changed is not None:
            self._on_lifecycle_changed()
        self.refresh()


__all__ = ["TaskBinding", "sanitize_error_message"]
