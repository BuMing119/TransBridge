"""Own background activation and the single Qt project-view installation."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.project_view_binding import ProjectViewState, prepare_project_view
from transbridge.ui.source_hydration import prepare_project_slots
from transbridge.ui.workers import ApiWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OpenedProjectView:
    result: object
    snapshot: object = None
    state: object = None


@dataclass(frozen=True, slots=True)
class PreparedWorkbenchView:
    slots: tuple
    state: ProjectViewState


class ProjectOpenOperation(QObject):
    def __init__(self, host, restore_sources, reopen) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.restore_sources = restore_sources
        self.reopen = reopen
        self.recovery_dialog = None
        self.worker = None
        self.token = None
        self.outcome = None
        self.error = None
        self._authority_committed = False

    def start(
        self,
        prepare,
        *,
        dirty_decision,
        success_verb,
        show_error_dialog=True,
        on_success=None,
        on_failure=None,
        request_context=None,
    ) -> bool:
        host = self.host
        if host.project_open_worker is not None or getattr(host.context, "project_opening", False):
            host.show_message("已有项目正在后台打开，请稍候。")
            return False
        if host.save_worker is not None or getattr(host, "foreground_worker", None) is not None:
            host.show_message("另一项后台操作仍在进行，请稍候再打开其他项目。")
            return False
        if getattr(host, "close_pending", False):
            return False
        self.success_verb = success_verb
        self.show_error_dialog = show_error_dialog
        self.on_success = on_success
        self.on_failure = on_failure
        self.outcome = self.error = None
        self._authority_committed = False
        self.token = None
        begin = getattr(host.context, "begin_project_open", None)
        projection = None
        expected = ...
        if begin is not None:
            projection = host.app_runtime.use_cases.resolve("project_projection")
            expected = (
                None
                if host.context.active_project_id is None
                else (
                    host.context.active_project_id,
                    host.context.project_revision,
                    host.context.active_variant_id,
                    host.context.variant_revision,
                )
            )
            self.token = begin()
        self._set_enabled(False)
        self._progress(0, 0, "正在校验并加载本地工程…")
        execution_context = host.runtime_context if request_context is None else request_context

        def run():
            prepared = prepare()
            if not prepared.is_success or prepared.value is None:
                return OpenedProjectView(prepared)
            worker.progress.emit(0, 0, "正在加载项目版本并准备工作台…")
            options = {"dirty_decision": dirty_decision}
            if self.token is not None:
                options.update(
                    prepare_view=lambda candidate: PreparedWorkbenchView(
                        prepare_project_slots(prepared.value.hydrations, candidate), prepare_project_view(candidate)
                    ),
                    expected_active_signature=expected,
                )
            opened = host.current_project_opener.activate(prepared.value, execution_context, **options)
            if not opened.is_success or opened.value is None or opened.value.get("recovery") is not None:
                return OpenedProjectView(opened)
            self._authority_committed = True
            snapshot = None if projection is None else projection.snapshot()
            state = None if self.token is None else opened.value["view_payload"].state.committed(snapshot)
            return OpenedProjectView(opened, snapshot, state)

        worker = ApiWorker(run, route_http_errors=False)
        self.worker = host.project_open_worker = worker
        worker.progress.connect(self._progress)
        worker.result.connect(self._received)
        worker.error.connect(self._failed)
        worker.finished.connect(self._finished)
        host.workers.append(worker)
        try:
            worker.start()
        except Exception as exc:
            logger.exception("Could not start the project-open worker")
            self.error = str(exc)
            self._finished()
            return False
        return True

    def _set_enabled(self, enabled):
        setter = getattr(self.host.workbench, "setEnabled", None)
        if setter is not None and not (enabled and getattr(self.host, "close_pending", False)):
            setter(enabled)

    def _progress(self, _current, _total, message):
        if getattr(self.host, "close_pending", False):
            return
        self.host.workbench.show_step2_progress(0, message)
        show = getattr(self.host, "show_project_open_progress", None)
        if show is not None:
            show(message)

    def _received(self, value):
        self.outcome = value

    def _failed(self, error):
        self.error = error

    def _finished(self):
        worker = self.worker
        opened = None
        failure = None
        try:
            if self.error is not None:
                failure = ("PROJECT_OPEN_FAILED", self.error)
            elif self.outcome is None:
                failure = ("PROJECT_OPEN_FAILED", "项目加载未返回结果，请重试。")
            else:
                bundle = self.outcome
                result = bundle.result
                if not result.is_success or result.value is None:
                    failure = (
                        (result.diagnostics[0].code, result.diagnostics[0].message)
                        if result.diagnostics
                        else ("PROJECT_OPEN_FAILED", "工程未能完成加载，请重试。")
                    )
                else:
                    opened = result.value
                    if opened.get("recovery") is None:
                        if self.token is not None:
                            installed = self.host.context.install_project_open(
                                self.token, bundle.snapshot, opened["view_payload"].slots, state=bundle.state
                            )
                            if not installed:
                                failure = ("PROJECT_VIEW_STALE", "工程已发生变化，请重新打开以加载最新内容。")
                        else:
                            error = self.restore_sources(opened["sources"], opened.get("hydrations"))
                            if error is not None:
                                failure = ("PROJECT_HYDRATION_FAILED", error)
        except Exception as exc:
            logger.exception("Could not install the prepared project view")
            failure = ("PROJECT_VIEW_INSTALL_FAILED", f"工作台安装失败：{exc}。请重新打开工程。")
        finally:
            if failure is not None and self._authority_committed:
                self.host.context.project_view_failed = True
                failure = (failure[0], f"工程已切换，但工作台恢复失败：{failure[1]}")
            if self.token is not None:
                try:
                    self.host.context.finish_project_open(self.token)
                    if failure is None and getattr(self.host.context, "project_view_failed", False):
                        failure = ("PROJECT_VIEW_STALE", "工程内容在工作台恢复期间发生变化，请重新打开工程。")
                except Exception as exc:
                    logger.exception("Could not finish the project view transaction")
                    self.host.context.project_view_failed = True
                    failure = ("PROJECT_VIEW_INSTALL_FAILED", f"工作台恢复未完成：{exc}。请重新打开工程。")
            self.token = None
            self.outcome = None
            if self.host.project_open_worker is worker:
                self.host.project_open_worker = None
            self.worker = None
            self.host.workbench.hide_step2_progress()
            hide = getattr(self.host, "hide_project_open_progress", None)
            if hide is not None:
                hide()
            self._set_enabled(not getattr(self.host.context, "project_view_failed", False))
        if failure is not None:
            self._show_failure(*failure)
        elif opened is not None and not getattr(self.host, "close_pending", False):
            recovery = opened.get("recovery")
            if recovery is not None:
                self._show_recovery(recovery)
            else:
                self.host.show_message(f"项目「{opened['name']}」{self.success_verb}")
                if self.on_success is not None:
                    self.on_success(opened)

    def _show_failure(self, code, message):
        if getattr(self.host, "close_pending", False):
            return
        if self.on_failure is None:
            self.host.show_message(f"{code}: {message}")
        if self.show_error_dialog and not getattr(self.host, "close_pending", False):
            QMessageBox.warning(self.host, "无法打开项目", message)
        if self.on_failure is not None:
            self.on_failure(code, message)

    def _show_recovery(self, recovery):
        from transbridge.ui.project_recovery import ProjectRecoveryDialog

        if self.on_failure is not None:
            self.on_failure("PROJECT_SOURCE_RECOVERY_AVAILABLE", "来源不可用，已打开只读恢复视图。")
        self.host.show_message(f"项目「{recovery.name}」已打开只读恢复视图，当前工程保持不变")
        dialog = ProjectRecoveryDialog(recovery, self.host)
        source_updates = getattr(self.host, "source_update_coordinator", None)
        if source_updates is not None:
            dialog.source_update_requested.connect(lambda: source_updates.start_recovery(recovery))
        else:
            dialog.update_source_button.setEnabled(False)
        self.recovery_dialog = dialog
        dialog.accepted.connect(lambda: QTimer.singleShot(0, lambda: self.reopen(recovery.project_path)))
        dialog.finished.connect(lambda _result: setattr(self, "recovery_dialog", None))
        dialog.open()
