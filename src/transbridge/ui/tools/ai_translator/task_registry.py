"""Application lifetime ownership and project-scoped access to AI runs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.ui.windowing import show_and_activate


def project_directory(ctx, runtime=None, runtime_context=None):
    if getattr(ctx, "project_opening", False):
        return None
    if runtime is not None and runtime_context is not None and "project_source_updates" in runtime.use_cases.names():
        context = replace(runtime_context, project_id=ctx.active_project_id, variant_id=None)
        path = runtime.use_cases.resolve("project_source_updates").active_project_path(context)
        return Path(path).parent if path else None
    return None


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
        self._recovery_requests = set()
        self._recovery_launches = {}
        self._recovery_workers = {}
        from .task_recovery_prompt import TaskRecoveryDiscovery

        self.recovery = TaskRecoveryDiscovery(self)

    def request_recovery(self, record, *, restart=False):
        from .task_recovery_prompt import RECOVERABLE_STATES, matches_version

        if (
            self.shutting_down
            or getattr(self.ctx, "project_opening", False)
            or self.is_recovery_active(record["task_id"])
            or (not restart and record.get("state") not in RECOVERABLE_STATES)
            or not record.get("supported", True)
            or not matches_version(record, getattr(self.ctx, "active_version_identity", None))
        ):
            return False
        if not self._retire_terminal_owner(record["task_id"]):
            return False
        self._recovery_requests.add(record["task_id"])
        if record.get("recovery_summary"):
            self._load_requested_recovery(record, restart)
            return True
        try:
            self.recovery_requested.emit(record, restart)
        except Exception:
            self.release_recovery_request(record["task_id"])
            raise
        return True

    def _load_requested_recovery(self, summary, restart):
        from transbridge.ui.workers import ApiWorker

        from .task_recovery_prompt import load_selected_recovery, matches_version

        task_id, directory = summary["task_id"], self.directory_provider()
        worker = ApiWorker(lambda: load_selected_recovery(Path(directory), summary), route_http_errors=False)
        self._recovery_workers[task_id] = worker

        def loaded(record):
            if (
                not self.shutting_down
                and directory == self.directory_provider()
                and matches_version(record, getattr(self.ctx, "active_version_identity", None))
            ):
                self.recovery_requested.emit(record, restart)
            else:
                self.release_recovery_request(task_id)

        def failed(error):
            self.release_recovery_request(task_id)
            summary.update(supported=False, recovery_error=str(error))
            self.recovery._failed(error)

        def finished():
            self._recovery_workers.pop(task_id, None)
            worker.deleteLater()
            self.changed.emit()

        worker.result.connect(loaded)
        worker.error.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _retire_terminal_owner(self, task_id):
        """Explicit continuation transfers the lease after all old writes settle."""
        retired = []
        for run_id, window in self.windows.items():
            request, run = window.request, window.run
            origin = getattr(request, "recovery_record", None) or {}
            identities = {
                run_id,
                getattr(request, "recovery_task_id", None),
                origin.get("task_id"),
                origin.get("origin_task_id"),
            }
            if task_id not in identities or run.state not in {"completed", "cancelled", "error"}:
                continue
            recovery = getattr(run, "recovery", None)
            evidence = getattr(getattr(window, "session", None), "merge_evidence_writer", None)
            if (
                getattr(run, "busy", False)
                or getattr(getattr(run, "records", None), "error", "")
                or getattr(recovery, "error", "")
                or getattr(evidence, "error", "")
                or any(getattr(dialog, "busy", False) for dialog in getattr(window, "_dialogs", ()))
            ):
                return False
            if callable(getattr(window, "dispose", None)):
                retired.append((run_id, window))
        # Validate every matching owner before disposing any of them. Active
        # result-review leases are already excluded by is_recovery_active.
        for run_id, window in retired:
            window.dispose()
            del self.windows[run_id]
        return True

    def release_recovery_request(self, task_id):
        self._recovery_requests.discard(task_id)

    def reserve_recovery_launch(self, task_id, launch):
        if self._recovery_launches.get(task_id) is launch:
            return True
        self.release_recovery_request(task_id)
        if self.is_recovery_active(task_id):
            return False
        self._recovery_launches[task_id] = launch
        self.changed.emit()
        return True

    def release_recovery_launch(self, task_id, launch):
        if self._recovery_launches.get(task_id) is launch:
            del self._recovery_launches[task_id]
            self.changed.emit()

    def is_recovery_active(self, task_id):
        return (
            task_id in self._recovery_requests
            or task_id in self._recovery_launches
            or any(
                getattr(getattr(dialog, "review", None), "task_id", None) == task_id and not dialog.review.closed
                for owner in (*self.windows.values(), *self.history_windows)
                for dialog in getattr(owner, "_dialogs", ())
            )
            or any(
                task_id
                in {
                    run_id,
                    getattr(window.request, "recovery_task_id", None),
                    (getattr(window.request, "recovery_record", None) or {}).get("task_id"),
                    (getattr(window.request, "recovery_record", None) or {}).get("origin_task_id"),
                }
                for run_id, window in self.windows.items()
                if getattr(window.run, "state", "") not in {"completed", "cancelled", "error"}
            )
        )

    def register(self, window):
        self.windows[window.request.run_id] = window
        window.run.setParent(self)
        window.run.changed.connect(self.changed)
        self.changed.emit()

    def current_windows(self):
        project_dir = self.directory_provider()
        identity = self.ctx.active_version_identity
        if identity is None:
            return ()
        return tuple(
            win
            for win in self.windows.values()
            if win.session.project_dir == project_dir and win.session.version_identity[0] == identity[0]
        )

    def foreground(self):
        for window in reversed(self.current_windows()):
            if window.is_running() or window.run.state == "pending_confirmation":
                show_and_activate(window)
                return True
        return False

    def open_history(self, *, run_id=None):
        if self.shutting_down:
            return
        from .task_history_dialog import TaskHistoryDialog

        dialog = TaskHistoryDialog(self, theme_view=self.theme_view, run_id=run_id)
        self.history_windows.append(dialog)
        show_and_activate(dialog)

    @property
    def busy(self):
        return (
            self.recovery.worker is not None
            or bool(self._recovery_workers)
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
        errors = tuple(
            message
            for window in self.windows.values()
            for message in (
                window.run.records.error,
                getattr(getattr(window.run, "recovery", None), "error", ""),
                getattr(getattr(getattr(window, "session", None), "merge_evidence_writer", None), "error", ""),
            )
            if message
        )
        return errors + tuple(
            writer.error
            for owner in (*self.windows.values(), *self.history_windows)
            for dialog in getattr(owner, "_dialogs", ())
            if (writer := getattr(getattr(dialog, "review", None), "evidence_writer", None)) is not None
            and writer.error
        )

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
