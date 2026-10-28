"""Main-window persistence, autosave and asynchronous close ownership."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QSettings, QTimer

from transbridge.ui.worker_registry import get_api_worker_registry
from transbridge.ui.workers import ApiWorker

from .close_input_guard import CloseInputGuard
from .close_progress import CloseProgress
from .save_recovery import SaveRecovery, choose_recovery_destination, write_recovery_bundle
from .task_close import TaskCloseGuard

logger = logging.getLogger(__name__)


class AutoSaveManager(QObject):
    """Manage periodic/debounced saves without owning project state."""

    def __init__(self, host, parent=None, *, debounce_ms: int = 10_000) -> None:
        super().__init__(parent)
        self._host = host
        self._debounce_ms = debounce_ms
        self._stopped = False
        self._failed_identity = None
        self._result_reported = False
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self.trigger_debounce)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._auto_save)
        for name in ("project_changed", "variant_changed"):
            signal = getattr(host.context, name, None)
            if signal is not None:
                signal.connect(self.trigger_debounce)

    def _identity(self):
        context = self._host.context
        return (
            getattr(context, "active_project_id", None),
            getattr(context, "active_variant_id", None),
        )

    def record_save_result(self, succeeded: bool) -> None:
        """Pause failed saves until an explicit successful save or a context switch."""
        self._result_reported = True
        self._failed_identity = None if succeeded else self._identity()
        self.trigger_debounce()

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
        if not context.dirty or self._failed_identity == self._identity():
            self._debounce_timer.stop()
            return
        self._failed_identity = None
        self._debounce_timer.start(self._debounce_ms)

    def _auto_save(self) -> None:
        if self._stopped or self._failed_identity == self._identity():
            return
        context = self._host.context
        if not context.dirty:
            return
        self._result_reported = False
        accepted = self._host.save_current_project_async(
            automatic=True,
            on_finished=self._on_auto_save_finished,
        )
        if not accepted and context.dirty:
            self._debounce_timer.start(self._debounce_ms)

    def _on_auto_save_finished(self, saved: bool) -> None:
        if not self._result_reported:
            self.record_save_result(saved)
        self.trigger_debounce()


class WindowLifecycle:
    def __init__(self, host) -> None:
        self._host = host
        self._close_pending = False
        self._close_ready = False
        self._attempt_id = 0
        self._progress = CloseProgress(host, self.cancel_close)
        self._discard_ai_record_errors = False
        self._recovery = SaveRecovery(host.context)
        self._recovery_destination = None
        self._recovery_worker = None
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
        self._attempt_id += 1
        self._recovery.reset()
        self._recovery_destination = None
        self._discard_ai_record_errors = False
        self._host.close_pending = True
        self._host.workbench.setEnabled(False)
        self._input_guard.suspend()
        self.auto_saver.stop()
        self._host.workbench.show_step2_progress(0, "正在保存并关闭…")
        self._progress.show()
        self.begin_background_close()
        return False

    @staticmethod
    def _running(worker) -> bool:
        return worker is not None and worker.isRunning()

    def begin_background_close(self) -> None:
        attempt = self._attempt_id
        if not self._close_pending or not self._check_inline_drafts():
            return
        if not self._is_attempt(attempt):
            return
        self._input_guard.suspend()
        # Issue cancellation immediately, including while legacy workers finish.
        runtime_ready = self._check_runtime_tasks()
        if not self._is_attempt(attempt):
            return
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        if ai_tasks is not None:
            ai_tasks.shutdown()
            if ai_tasks.busy:
                self._host.workbench.show_step2_progress(0, "正在结束 AI 任务…")
                self._schedule(100, self.begin_background_close)
                return
            if not self._check_record_errors(ai_tasks):
                return
        if not runtime_ready:
            self._host.workbench.show_step2_progress(0, "正在取消后台任务并清理未提交内容…")
            self._schedule(100, self.begin_background_close)
            return
        if self._host.project_open_worker is not None or getattr(self._host.context, "project_opening", False):
            self._schedule(100, self.begin_background_close)
            return
        if self._running(self._host.foreground_worker):
            self._schedule(100, self.begin_background_close)
            return
        if get_api_worker_registry().busy or self._input_guard.awaiting_dialog:
            self._host.workbench.show_step2_progress(0, "正在等待后台任务完成…")
            self._schedule(100, self.begin_background_close)
            return
        if not self._host.save_current_project_async(on_finished=self._guarded(self.finish_background_close)):
            self._schedule(0, self.begin_background_close)

    def finish_background_close(self, saved: bool) -> None:
        attempt = self._attempt_id
        if not self._ready_to_dispose(saved) or not self._is_attempt(attempt):
            return
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        if ai_tasks is not None:
            if not self._check_record_errors(ai_tasks):
                return
        if not self._is_attempt(attempt) or not self._check_inline_drafts():
            return
        # Both confirmations run nested event loops. Their answer does not
        # authorize discarding later edits or disposing a newly started worker.
        if not self._is_attempt(attempt) or not self._ready_to_dispose(saved):
            return
        if not self._input_guard.close_windows(exclude=self._host):
            if self._is_attempt(attempt):
                self._schedule(100, lambda: self.finish_background_close(saved))
            return
        if not self._is_attempt(attempt) or not self._ready_to_dispose(saved):
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
            self._progress.hide()
            self._host.close()

    def _ready_to_dispose(self, saved: bool) -> bool:
        attempt = self._attempt_id
        if not self._close_pending:
            return False
        if not self._check_runtime_tasks():
            if self._is_attempt(attempt):
                self._schedule(100, lambda: self.finish_background_close(saved))
            return False
        if not self._is_attempt(attempt):
            return False
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        # Saving itself is an ApiWorker. Also drain native completion handlers,
        # follow-up workers, and durable AI records queued by its observers.
        if (
            get_api_worker_registry().busy
            or self._input_guard.awaiting_dialog
            or self._running(self._host.project_open_worker)
            or getattr(self._host.context, "project_opening", False)
            or self._running(self._host.foreground_worker)
            or (ai_tasks is not None and ai_tasks.busy)
        ):
            self._input_guard.suspend()
            self._schedule(100, lambda: self.finish_background_close(saved))
            return False
        if not saved or self._host.context.dirty:
            if self._recovery.verified:
                return True
            if self._recovery.has_backup:
                self._recovery_failed("备份完成后内容又有变化，窗口保持打开。请重新保存或备份后退出。")
                return False
            if self._recovery_destination is None:
                self._input_guard.resume()
                destination = choose_recovery_destination(self._host)
                if not self._is_attempt(attempt):
                    return False
                if destination is None:
                    self._cancel_close()
                    return False
                self._recovery_destination = destination
                self._input_guard.suspend()
                # The dialogs run nested event loops; drain any new work before capturing.
                self._schedule(0, lambda: self.finish_background_close(False))
            elif self._recovery_worker is None:
                self._start_recovery()
            return False
        return True

    def _start_recovery(self) -> None:
        try:
            capture = self._recovery.capture()
        except Exception as exc:
            logger.exception("Cannot capture project recovery")
            self._recovery_failed(f"无法备份当前内容：{exc}")
            return
        self._host.workbench.show_step2_progress(0, "正在备份全部翻译内容并校验…")
        worker = None
        outcome = {}
        attempt = self._attempt_id
        destination = self._recovery_destination

        def finished():
            if self._recovery_worker is worker:
                self._recovery_worker = None
            if attempt != self._attempt_id or not self._close_pending:
                return
            if "error" in outcome or "path" not in outcome:
                self._recovery_failed(f"恢复备份失败，窗口保持打开：{outcome.get('error', '未收到备份结果')}")
            elif not self._recovery.accept(capture):
                self._recovery_failed(f"备份期间内容有变化，窗口保持打开。已导出的副本：{outcome['path']}")
            else:
                self.finish_background_close(False)

        try:
            worker = ApiWorker(
                lambda: write_recovery_bundle(destination, capture),
                route_http_errors=False,
            )
            self._recovery_worker = worker
            worker.result.connect(lambda path: outcome.__setitem__("path", path))
            worker.error.connect(lambda error: outcome.__setitem__("error", error))
            worker.finished.connect(finished)
            worker.start()
        except Exception as exc:
            logger.exception("Cannot start recovery export")
            self._recovery_worker = None
            if worker is not None:
                worker.deleteLater()
            self._recovery_failed(f"无法开始恢复备份，窗口保持打开：{exc}")

    def _recovery_failed(self, message: str) -> None:
        self._cancel_close()
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.warning(self._host, "无法关闭", message)

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

        attempt = self._attempt_id
        self._input_guard.resume()
        answer = QMessageBox.question(
            self._host,
            "任务记录未保存",
            "有任务记录保存失败。仍要退出？",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if not self._is_attempt(attempt):
            return False
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
        attempt = self._attempt_id
        self._input_guard.resume()
        confirmed = confirm()
        if not self._is_attempt(attempt):
            return False
        if confirmed:
            self._input_guard.suspend()
            return True
        self._cancel_close()
        return False

    def _cancel_close(self) -> None:
        self._attempt_id += 1
        if self._task_close is not None:
            self._task_close.abort()
        self._close_pending = self._host.close_pending = False
        self._recovery.reset()
        self._recovery_destination = None
        self._input_guard.resume()
        self._progress.hide()
        self._host.workbench.setEnabled(
            not (
                getattr(self._host.context, "project_opening", False)
                or getattr(self._host.context, "project_view_failed", False)
            )
        )
        self._host.workbench.hide_step2_progress()
        self.auto_saver.start()
        ai_tasks = getattr(self._host.workbench, "ai_tasks", None)
        if ai_tasks is not None:
            ai_tasks.resume()

    def cancel_close(self) -> None:
        """Return to work without reviving cancelled tasks or dropping results."""
        if self._close_pending and not self._close_ready:
            self._cancel_close()

    def _guarded(self, callback):
        attempt = self._attempt_id

        def invoke(*args):
            if self._is_attempt(attempt):
                callback(*args)

        return invoke

    def _is_attempt(self, attempt: int) -> bool:
        return self._close_pending and attempt == self._attempt_id

    def _schedule(self, delay: int, callback) -> None:
        QTimer.singleShot(delay, self._guarded(callback))
