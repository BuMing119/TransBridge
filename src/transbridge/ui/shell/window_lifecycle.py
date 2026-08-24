"""Main-window persistence, autosave and asynchronous close ownership."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QSettings, QTimer


class AutoSaveManager(QObject):
    """Manage periodic/debounced saves without owning project state."""

    def __init__(self, host, parent=None, *, debounce_ms: int = 10_000) -> None:
        super().__init__(parent)
        self._host = host
        self._debounce_ms = debounce_ms
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self.trigger_debounce)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._auto_save)

    def start(self, interval_minutes: int = 5) -> None:
        self._interval_timer.start(interval_minutes * 60_000)

    def stop(self) -> None:
        self._interval_timer.stop()
        self._debounce_timer.stop()

    def trigger_debounce(self) -> None:
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
        accepted = self._host.save_current_project_async(automatic=True)
        if not accepted and context.dirty:
            self._debounce_timer.start(self._debounce_ms)


class WindowLifecycle:
    def __init__(self, host) -> None:
        self._host = host
        self._close_pending = False
        self._close_ready = False
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
        self._host.close_pending = True
        self.auto_saver.stop()
        self._host.workbench.show_step2_progress(0, "正在保存并关闭…")
        if self._running(self._host.project_open_worker):
            self._host.project_open_worker.finished.connect(self.begin_background_close)
        elif self._running(self._host.foreground_worker):
            self._host.foreground_worker.finished.connect(self.begin_background_close)
        else:
            self.begin_background_close()
        return False

    @staticmethod
    def _running(worker) -> bool:
        return worker is not None and worker.isRunning()

    def begin_background_close(self) -> None:
        if self._running(self._host.project_open_worker):
            self._host.project_open_worker.finished.connect(self.begin_background_close)
            return
        if self._running(self._host.foreground_worker):
            self._host.foreground_worker.finished.connect(self.begin_background_close)
            return
        if not self._host.save_current_project_async(on_finished=self.finish_background_close):
            QTimer.singleShot(0, self.begin_background_close)

    def finish_background_close(self, saved: bool) -> None:
        if not saved:
            self._close_pending = False
            self._host.close_pending = False
            self._host.workbench.setEnabled(True)
            self._host.workbench.hide_step2_progress()
            self.auto_saver.start()
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(self._host, "无法关闭", "项目保存失败，窗口保持打开以避免数据丢失。")
            return
        try:
            self._host.project_coordinator.save_workspace_session()
            if self._host.context.workspace:
                self._host.context.workspace.save()
            settings = QSettings("TransBridge", "MainWindow")
            settings.setValue("geometry", self._host.saveGeometry())
            settings.setValue("state", self._host.saveState())
            self._host.tool_windows.dispose(wait_for_worker=False)
            self._host.context.close_projection()
            self._host.status_presenter.close()
        finally:
            self._host.workbench.hide_step2_progress()
            self._close_ready = True
            self._host.close_ready = True
            self._host.close()
