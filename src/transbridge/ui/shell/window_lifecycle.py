"""Main-window persistence, autosave and asynchronous close ownership."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QSettings, QTimer

from transbridge.ui.worker_registry import get_api_worker_registry

from .close_input_guard import CloseInputGuard
from .task_close import TaskCloseGuard

logger = logging.getLogger(__name__)


class AutoSaveManager(QObject):
    """Manage periodic/debounced saves without owning project state."""

    def __init__(self, host, parent=None, *, debounce_ms: int = 10_000) -> None:
        super().__init__(parent)
        self._host = host
        self._debounce_ms = debounce_ms
        self._stopped = False
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self.trigger_debounce)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._auto_save)

    def start(self, interval_minutes: int = 5) -> None:
        self._stopped = False
        self._interval_timer.start(interval_minutes * 60_000)

    def stop(self) -> None:
        self._stopped = True
        self._interval_timer.stop()
        self._debounce_timer.stop()

    def trigger_debounce(self) -> None:
        if self._stopped:
            return
        context = self._host.context
        if not context.dirty:
            self._debounce_timer.stop()
            return
        self._debounce_timer.start(self._debounce_ms)

    def _auto_save(self) -> None:
        context = self._host.context
        variant_store = context.variant_store
        if context.uses_authoritative_projection:
            if not context.dirty:
                return
        elif variant_store is None or not variant_store.dirty:
            return
        accepted = self._host.save_current_project_async(
            automatic=True,
            on_finished=self._on_auto_save_finished,
        )
        if not accepted and context.dirty:
            self._debounce_timer.start(self._debounce_ms)

    def _on_auto_save_finished(self, _saved: bool) -> None:
        if not self._stopped and self._host.context.dirty:
            self._debounce_timer.start(self._debounce_ms)


class WindowLifecycle:
    def __init__(self, host) -> None:
        self._host = host
        self._close_pending = False
        self._close_ready = False
        self._discard_ai_record_errors = False
        self._input_guard = CloseInputGuard()
        runtime = getattr(getattr(host, "app_runtime", None), "tasks", None)
        self._task_close = TaskCloseGuard(runtime) if runtime is not None else None
        self.auto_saver = AutoSaveManager(host, host)

    def start(self) -> None:
        self.auto_saver.start()
        self._host.context.dirty_changed.connect(self.auto_saver.trigger_debounce)

    def restore_state(self) -> None:
        settings = QSettings("TransBridge", "MainWindow")
        if settings.contains("geometry"):
            self._host.restoreGeometry(settings.value("geometry"))
        if settings.contains("state"):
            self._host.restoreState(settings.value("state"))

    def close_event(self, event) -> bool:
        if self._close_ready:
            return True
        event.ignore()
        if self._close_pending:
            return False
        self._close_pending = True
        self._discard_ai_record_errors = False
        self._host.close_pending = True
        self._host.workbench.setEnabled(False)
        self._input_guard.suspend()
        self.auto_saver.stop()
        self._host.workbench.show_step2_progress(0, "正在保存并关闭…")
        self.begin_background_close()
        return False

    @staticmethod
    def _running(worker) -> bool:
        return worker is not None and worker.isRunning()

    def begin_background_close(self) -> None:
        if not self._close_pending or not self._check_inline_drafts():
            return
        self._input_guard.suspend()
        # Issue cancellation immediately, including while legacy workers finish.
        runtime_ready = self._check_runtime_tasks()
        if not self._close_pending:
            return
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        if ai_tasks is not None:
            ai_tasks.shutdown()
            if ai_tasks.busy:
                self._host.workbench.show_step2_progress(0, "正在结束 AI 任务…")
                QTimer.singleShot(100, self.begin_background_close)
                return
            if not self._check_record_errors(ai_tasks):
                return
        if not runtime_ready:
            self._host.workbench.show_step2_progress(0, "正在取消后台任务并清理未提交内容…")
            QTimer.singleShot(100, self.begin_background_close)
            return
        if self._running(self._host.project_open_worker):
            self._host.project_open_worker.finished.connect(self.begin_background_close)
            return
        if self._running(self._host.foreground_worker):
            self._host.foreground_worker.finished.connect(self.begin_background_close)
            return
        if get_api_worker_registry().busy or self._input_guard.awaiting_dialog:
            self._host.workbench.show_step2_progress(0, "正在等待后台任务完成…")
            QTimer.singleShot(100, self.begin_background_close)
            return
        if not self._host.save_current_project_async(on_finished=self.finish_background_close):
            QTimer.singleShot(0, self.begin_background_close)

    def finish_background_close(self, saved: bool) -> None:
        if not self._ready_to_dispose(saved):
            return
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        if ai_tasks is not None:
            if not self._check_record_errors(ai_tasks):
                return
        if not self._check_inline_drafts():
            return
        # Both confirmations run nested event loops. Their answer does not
        # authorize discarding later edits or disposing a newly started worker.
        if not self._ready_to_dispose(saved):
            return
        if not self._input_guard.close_windows(exclude=self._host):
            QTimer.singleShot(100, lambda: self.finish_background_close(saved))
            return
        if not self._ready_to_dispose(saved):
            return
        try:
            self._host.project_coordinator.save_workspace_session()
            if self._host.context.workspace:
                self._host.context.workspace.save()
            settings = QSettings("TransBridge", "MainWindow")
            settings.setValue("geometry", self._host.saveGeometry())
            settings.setValue("state", self._host.saveState())
            self._host.tool_windows.dispose(wait_for_worker=False)
            ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
            if ai_tasks is not None:
                ai_tasks.dispose()
            self._host.context.close_projection()
            self._host.status_presenter.close()
        finally:
            self._host.workbench.hide_step2_progress()
            self._close_ready = True
            self._host.close_ready = True
            self._input_guard.resume()
            self._host.close()

    def _ready_to_dispose(self, saved: bool) -> bool:
        if not self._close_pending:
            return False
        if not self._check_runtime_tasks():
            if self._close_pending:
                QTimer.singleShot(100, lambda: self.finish_background_close(saved))
            return False
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        # Saving itself is an ApiWorker. Also drain native completion handlers,
        # follow-up workers, and durable AI records queued by its observers.
        if (
            get_api_worker_registry().busy
            or self._input_guard.awaiting_dialog
            or self._running(self._host.project_open_worker)
            or self._running(self._host.foreground_worker)
            or (ai_tasks is not None and ai_tasks.busy)
        ):
            self._input_guard.suspend()
            QTimer.singleShot(100, lambda: self.finish_background_close(saved))
            return False
        if not saved or self._host.context.dirty:
            self._cancel_close()
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self._host,
                "无法关闭",
                "项目仍有未保存的更改或保存失败，窗口保持打开以避免数据丢失。",
            )
            return False
        return True

    def _check_runtime_tasks(self) -> bool:
        if self._task_close is None:
            return True
        try:
            return self._task_close.ready()
        except Exception as exc:
            logger.exception("Cannot safely finish application close")
            self._cancel_close()
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(self._host, "无法关闭", f"{exc}\n窗口保持打开，已完成的结果会保留。")
            return False

    def _check_record_errors(self, ai_tasks) -> bool:
        if self._discard_ai_record_errors or not ai_tasks.record_errors:
            return True
        from PyQt6.QtWidgets import QMessageBox

        self._input_guard.resume()
        answer = QMessageBox.question(
            self._host,
            "任务记录未保存",
            "有任务记录保存失败。仍要退出？",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Discard:
            self._discard_ai_record_errors = True
            self._input_guard.suspend()
            return True
        self._cancel_close()
        return False

    def _check_inline_drafts(self) -> bool:
        confirm = getattr(self._host.workbench, "confirm_inline_edit_close", None)
        if confirm is None:
            return True
        self._input_guard.resume()
        if confirm():
            self._input_guard.suspend()
            return True
        self._cancel_close()
        return False

    def _cancel_close(self) -> None:
        if self._task_close is not None:
            self._task_close.abort()
        self._close_pending = self._host.close_pending = False
        self._host.workbench.setEnabled(True)
        self._input_guard.resume()
        self._host.workbench.hide_step2_progress()
        self.auto_saver.start()
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        if ai_tasks is not None:
            ai_tasks.resume()
