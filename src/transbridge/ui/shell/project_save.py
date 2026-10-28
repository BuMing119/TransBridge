"""Own coalesced current-project saves and Qt completion."""

import logging

from PyQt6.QtCore import QObject, QTimer

_logger = logging.getLogger(__name__)


class ProjectSaveCoordinator(QObject):
    def __init__(self, host):
        super().__init__(host if isinstance(host, QObject) else None)
        self._host = host
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._try_start)
        self._waiting = False
        self._closed = False
        self._automatic = True
        self._target = None
        self._worker_factory = None
        if isinstance(host, QObject):
            host.destroyed.connect(self.close)

    def _identity(self):
        return self._host._ctx.active_version_identity

    def request(self, *, automatic, on_finished, worker_factory):
        host, context = self._host, self._host._ctx
        if self._closed:
            if on_finished is not None:
                QTimer.singleShot(0, lambda: on_finished(False))
            return True
        if getattr(context, "project_view_failed", False):
            safe = getattr(host, "_close_pending", False) and not context.dirty
            if not automatic and not safe:
                host.show_message("工作台未能加载，请重新打开工程后再保存。")
            if on_finished is not None:
                QTimer.singleShot(0, lambda: on_finished(safe))
            return True
        if host._foreground_worker is not None and host._foreground_worker.isRunning():
            if not automatic:
                host.show_message("另一项后台操作仍在进行，请稍候。")
            return False
        if host._project_open_worker is not None or getattr(context, "project_opening", False):
            if not automatic:
                host.show_message("项目仍在打开，请稍候。")
            return False
        if self._waiting and self._identity() != self._target:
            self._finish(False, context, self._automatic, cancelled=True)
        if on_finished is not None:
            host._save_callbacks.append(on_finished)
        if host._save_worker is not None:
            return True
        if self._waiting:
            if not automatic and self._automatic:
                self._automatic = False
                self._show_saving(False)
            return True
        self._waiting, self._automatic = True, automatic
        self._target, self._worker_factory = self._identity(), worker_factory
        self._show_saving(automatic)
        self._try_start()
        return True

    def _show_saving(self, automatic):
        workbench = self._host._workbench
        if not automatic:
            workbench.show_step2_progress(0, "正在保存项目…")
            workbench.setEnabled(False)
        saving = getattr(workbench.project_bar, "set_save_saving", None)
        if saving is not None:
            saving()

    def _try_start(self):
        if not self._waiting or self._closed:
            return
        host, context = self._host, self._host._ctx
        if self._identity() != self._target:
            self._finish(False, context, self._automatic, cancelled=True)
            return
        if (
            host._project_open_worker is not None
            or getattr(context, "project_opening", False)
            or (host._foreground_worker is not None and host._foreground_worker.isRunning())
        ):
            self._timer.start(25)
            return
        self._waiting = False
        self._launch(host._save_current_project, context, self._automatic)

    def _launch(self, operation, context, automatic):
        host, worker, succeeded = self._host, None, False

        def saved(result):
            nonlocal succeeded
            if hasattr(result, "is_success") and not result.is_success:
                diagnostic = result.diagnostics[0]
                self._error(f"{diagnostic.code}: {diagnostic.message}", display=diagnostic.message)
                return
            succeeded = True

        def finished():
            if host._save_worker is worker:
                host._save_worker = None
            self._finish(succeeded, context, automatic)

        try:
            worker = self._worker_factory(operation, route_http_errors=False)
            host._save_worker = worker
            worker.result.connect(saved)
            worker.error.connect(self._error)
            worker.finished.connect(finished)
            worker.start()
        except Exception as exc:
            _logger.exception("Cannot start project save worker")
            self._error(f"无法开始保存：{exc}")
            finished()
            if worker is not None:
                worker.deleteLater()
            return
        host._workers.append(worker)

    def _error(self, message, *, display=None):
        if self._closed:
            return
        failed = getattr(self._host._workbench.project_bar, "set_save_failed", None)
        if failed is not None:
            failed(message if display is None else display)
        self._host.show_message(f"保存失败：{message}" if display is None else message, error=True)

    def _finish(self, succeeded, context, automatic, *, cancelled=False):
        self._waiting = False
        if not self._closed:
            self._timer.stop()
        host = self._host
        current = False
        callbacks, host._save_callbacks = host._save_callbacks, []
        try:
            if not self._closed:
                cancelled = cancelled or self._identity() != self._target
                dirty = bool(context.dirty)
                current = succeeded and not dirty and not cancelled
                auto_saver = getattr(host, "_auto_saver", None)
                if auto_saver is not None and not cancelled:
                    auto_saver.record_save_result(succeeded)
                if succeeded or cancelled:
                    host._workbench.project_bar.set_save_completed(dirty=dirty)
                if succeeded and not automatic and not cancelled:
                    if current:
                        host._workbench.project_bar.flash_saved()
                        host.show_message("项目已保存")
                    else:
                        host.show_message("保存完成，但仍有新的更改待保存")
                if not automatic and host._foreground_worker is None:
                    host._workbench.hide_step2_progress()
                if not automatic and not host._close_pending:
                    host._workbench.setEnabled(True)
        except Exception:
            _logger.exception("Cannot acknowledge project save; current changes remain unsaved")
            current = False
        finally:
            for callback in callbacks:
                try:
                    callback(current)
                except Exception:
                    _logger.exception("Project save completion callback failed")

    def close(self, *_args):
        self._closed = True
        self._timer.stop()
        if self._waiting:
            self._finish(False, self._host._ctx, self._automatic, cancelled=True)
