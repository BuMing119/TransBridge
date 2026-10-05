"""Application lifetime ownership and project-scoped access to AI runs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.ui.windowing import show_and_activate


def project_directory(ctx, runtime=None, runtime_context=None):
    if runtime is not None and runtime_context is not None and "project_source_updates" in runtime.use_cases.names():
        context = replace(runtime_context, project_id=ctx.active_project_id, variant_id=None)
        path = runtime.use_cases.resolve("project_source_updates").active_project_path(context)
        return Path(path).parent if path else None
    project = getattr(ctx, "active_project", None)
    path = getattr(project, "project_dir", None)
    return Path(path) if path else None


class AiTaskRegistry(QObject):
    changed = pyqtSignal()
    recovery_requested = pyqtSignal(object, bool)

    def __init__(self, ctx, parent=None, *, directory_provider=None, theme_view=None):
        super().__init__(parent)
        self.ctx = ctx
        self.directory_provider = directory_provider or (lambda: project_directory(ctx))
        self.theme_view = theme_view
        self.windows = {}
        self.history_windows = []
        self.shutting_down = False
        self.recovery_error = ""
        from .task_recovery_prompt import TaskRecoveryDiscovery

        self.recovery = TaskRecoveryDiscovery(self)

    def request_recovery(self, record, *, restart=False):
        from .task_recovery_prompt import RECOVERABLE_STATES, matches_version

        if (
            self.shutting_down
            or self.is_recovery_active(record["task_id"])
            or (not restart and record.get("state") not in RECOVERABLE_STATES)
            or not record.get("supported", True)
            or not matches_version(record, getattr(self.ctx, "active_version_identity", None))
        ):
            return False
        self.recovery_requested.emit(record, restart)
        return True

    def is_recovery_active(self, task_id):
        return any(
            (getattr(window.request, "recovery_task_id", None) or run_id) == task_id
            for run_id, window in self.windows.items()
            if getattr(window.run, "state", "") not in {"completed", "cancelled", "error"}
        )

    def register(self, window):
        self.windows[window.request.run_id] = window
        window.run.setParent(self)
        window.run.changed.connect(self.changed)
        self.changed.emit()

    def current_windows(self):
        project_dir = self.directory_provider()
        return tuple(win for win in self.windows.values() if win.session.project_dir == project_dir)

    def foreground(self):
        for window in reversed(self.current_windows()):
            if window.is_running() or window.run.state == "pending_confirmation":
                show_and_activate(window)
                return True
        return False

    def open_history(self):
        if self.shutting_down:
            return
        from .task_history_dialog import TaskHistoryDialog

        dialog = TaskHistoryDialog(self, theme_view=self.theme_view)
        self.history_windows.append(dialog)
        show_and_activate(dialog)

    @property
    def busy(self):
        return (
            self.recovery.worker is not None
            or any(window.run.busy for window in self.windows.values())
            or any(
                getattr(dialog, "busy", False)
                for window in (*self.windows.values(), *self.history_windows)
                for dialog in (window, *getattr(window, "_dialogs", ()))
                if not hasattr(dialog, "run")
            )
        )

    def shutdown(self):
        self.shutting_down = True
        self.recovery.shutdown()
        for window in self.windows.values():
            window.run.shutdown()
        for window in (*self.windows.values(), *self.history_windows):
            for dialog in (window, *getattr(window, "_dialogs", ())):
                if hasattr(dialog, "shutdown"):
                    dialog.shutdown()

    @property
    def record_errors(self):
        return tuple(window.run.records.error for window in self.windows.values() if window.run.records.error)

    def resume(self):
        self.shutting_down = False
        self.recovery.schedule()
        for window in self.windows.values():
            window.run.shutting_down = False
        for window in (*self.windows.values(), *self.history_windows):
            for dialog in getattr(window, "_dialogs", ()):
                if hasattr(dialog, "resume_exports"):
                    dialog.resume_exports()

    def dispose(self):
        if self.busy:
            raise RuntimeError("AI 任务尚未结束")
        self.recovery.shutdown()
        for window in self.windows.values():
            window.dispose()
        for dialog in self.history_windows:
            for result in dialog._dialogs:
                result.close()
            dialog.close()
