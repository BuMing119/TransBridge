"""Asynchronous startup with a bounded GUI capture and guarded publication."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import logging
from types import SimpleNamespace
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from transbridge.ui.windowing import show_and_activate

from .run_preparation import confirm_optional_retrieval
from .run_request_preparation import capture_terminology_owner, prepare_run_request
from .run_spec import FrozenExecutionConfig, preflight_ai_run
from .source_terms import source_terminology_path
from .task_consistency import TaskConsistency
from .task_result_preparation import TaskResultPreparation
from .term_source_inspector import TermSourceInspector

logger = logging.getLogger(__name__)
_ACTIVE_LAUNCHES = set()


class LaunchError(RuntimeError):
    def __init__(self, title, message):
        super().__init__(message)
        self.title = title


def inspect_launch(mode, config, tasks, remote, owner_id, options):
    """Inspect files and prepare identities using only detached task inputs."""
    options = dict(options)
    record = options.pop("reference_recovery_record", None)
    directory = options.pop("reference_recovery_directory", None)
    if record is not None:
        from .task_references import restored_reference_binding

        options["restored_terminology_binding"] = restored_reference_binding(
            record,
            directory,
            project_id=options.get("project_id"),
            variant_id=options.get("variant_id"),
            target_lang=getattr(config, "target_lang", ""),
        )
    optional = []
    for task in tasks:
        result = preflight_ai_run(
            mode, config, task.entries, esp_path=task.esp_path, mixed_has_translation=bool(task.translate_entries)
        )
        if result.blocking_issues:
            return (f"{task.label}：{result.blocking_reason}", (), False, None)
        optional.extend(result.optional_issues)
    if optional:
        return (None, tuple(optional), False, None)
    empty = (
        any(task.translate_entries for task in tasks)
        and options.get("restored_terminology_binding") is None
        and not remote
        and all(
            TermSourceInspector.all_empty(config, source_terminology_path(task.esp_path, task.key)) for task in tasks
        )
    )
    prepared = prepare_run_request(owner_id, mode, config, (e for task in tasks for e in task.entries), **options)
    return (None, (), empty, prepared)


class TaskLaunch(QObject):
    """Retain workers beyond window close; publish only to the captured version."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._recovery_registry = getattr(window, "_task_recovery_registry", None)
        self.controller = window._run_controller
        self.worker = TaskResultPreparation(self)
        self.capture = self.session = self.request = self.activity = self.client = self.progress = None
        self.cancelled = self.finished = False
        self._reservation_task_id = None
        self.dialog = QProgressDialog(
            "正在准备 AI 任务…", "取消", 0, 0, window if isinstance(window, QWidget) else None
        )
        self.dialog.setWindowTitle("准备 AI 任务")
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        if isinstance(window, QObject):
            window.destroyed.connect(self.cancel)

    def start(self):
        selection = getattr(self.window, "_recovery_selection", None)
        registry = self._recovery_registry
        if selection and registry is not None and hasattr(registry, "reserve_recovery_launch"):
            task_id = selection[0]["task_id"]
            if not registry.reserve_recovery_launch(task_id, self):
                self._failed("此任务已在准备或执行中，请查看现有任务。")
                return
            self._reservation_task_id = task_id
        _ACTIVE_LAUNCHES.add(self)
        self.dialog.show()
        QTimer.singleShot(0, self._capture)

    def cancel(self):
        if self.finished:
            return
        self.cancelled = True
        if self.capture is not None:
            self.capture.cancel()
        if not self.worker.busy and (self.capture is None or not self.capture.busy):
            self._finish()

    def _capture(self):
        if self.cancelled:
            self._finish()
            return
        try:
            from transbridge.ui.paratranz.target_context import bound_paratranz_project

            from .task_recovery_binding import complete_empty_recovery, recovery_sources
            from .task_session import TaskSession
            from .task_source_selection import capture_selection_intent

            window = self.window
            self.config = FrozenExecutionConfig(window._config_presenter.build()).copy()
            self.selection = getattr(window, "_recovery_selection", None)
            self.mode = self.selection[0]["mode"] if self.selection else window._view_port.mode
            registry = getattr(window, "_task_recovery_registry", None)
            if (
                self.selection
                and registry is not None
                and any(
                    (getattr(item.request, "recovery_task_id", None) or item.request.run_id)
                    == self.selection[0]["task_id"]
                    and not item.run.cancelled
                    and not item.session.completed
                    for item in registry.windows.values()
                )
            ):
                raise RuntimeError("此任务已在当前程序中打开，请从 AI 任务记录查看或继续现有任务。")
            # Production windows capture intent first. Selection then runs over
            # the same detached snapshot used by the session's final guard.
            selection_intent = capture_selection_intent(window, self.config) if hasattr(window, "_task_scope") else None
            tasks = (
                ()
                if selection_intent is not None
                else tuple(task for task in recovery_sources(window, self.config) if task.entries)
            )
            if selection_intent is None and not tasks:
                if not complete_empty_recovery(window, tasks):
                    raise LaunchError("AI 翻译", "所选来源没有符合范围的可处理词条。")
                self._finish()
                return
            ctx = window._ctx
            self.remote = deepcopy(bound_paratranz_project(ctx))
            self.remote_config = FrozenExecutionConfig(ctx.config).copy() if self.remote else None
            self.owner = capture_terminology_owner(ctx)
            self.options = dict(
                overwrite=(
                    bool(self.selection[0].get("overwrite", False))
                    if self.selection and not self.selection[1] and not self.selection[0].get("legacy")
                    else window._view_port.overwrite
                ),
                esp_path=tasks[0].esp_path if tasks else None,
                project_id=getattr(ctx, "active_project_id", None),
                variant_id=getattr(ctx, "active_variant_id", None),
                run_id=f"ai-{uuid4().hex}",
                terminology_owner=self.owner,
            )
            provider = getattr(window, "_task_project_directory_provider", None)
            self.capture = TaskSession.create_async(
                ctx,
                tasks,
                SimpleNamespace(mode=self.mode, run_id=self.options["run_id"]),
                project_dir=provider() if provider is not None else None,
                on_success=self._captured,
                on_error=self._failed,
                parent=self,
                selection_intent=selection_intent,
            )
        except Exception as exc:
            self._failed(str(exc), title=getattr(exc, "title", "AI 任务未启动"))

    def _captured(self, session):
        self.session = session
        if self.cancelled:
            self._finish()
            return
        intent = getattr(session, "selection_intent", None)
        if intent is not None:
            self.selection = intent.recovery
        if not session.tasks:
            if self.selection is None:
                self._failed("所选来源没有符合范围的可处理词条。", title="AI 翻译")
                return
            from .task_empty_recovery import EmptyRecoveryCompletion

            operation = EmptyRecoveryCompletion(session.project_dir, self.selection)
            self.dialog.setLabelText("正在结束已完成的恢复任务…")
            self.worker.start(operation.prepare, on_success=self._empty_prepared, on_error=self._failed)
            return
        self.options["esp_path"] = session.tasks[0].esp_path
        self._inspect()

    def _empty_prepared(self, prepared):
        if self.cancelled:
            self._finish()
            return
        try:
            self.session.require_current()
            # The captured task is complete at this guarded GUI commit point.
            # A later edit belongs to subsequent work and cannot undo this fact.
            self.dialog.setLabelText("当前恢复任务已确认完成，正在保存记录…")
            self.dialog.setCancelButton(None)
            self.worker.start(prepared.commit, on_success=self._empty_completed, on_error=self._failed)
        except Exception as exc:
            self._failed(str(exc))

    def _empty_completed(self, _result):
        try:
            if not self.cancelled:
                from .task_empty_recovery import publish_empty_recovery

                publish_empty_recovery(self.window)
        except Exception as exc:
            logger.exception("Completed recovery could not refresh its windows")
            QMessageBox.warning(self.window, "任务已完成", f"任务记录已完成，界面刷新失败：{exc}")
        finally:
            self._finish()

    def _inspect(self):
        self.dialog.setLabelText("正在检查运行条件和术语…")
        mode, config, tasks, remote = self.mode, self.config, self.session.tasks, self.remote
        owner_id, options = self.controller.owner_id, dict(self.options)
        if self.selection and not self.selection[1]:
            options["reference_recovery_record"] = self.selection[0]
            options["reference_recovery_directory"] = self.session.project_dir
        self.worker.start(
            lambda: inspect_launch(mode, config, tasks, remote, owner_id, options),
            on_success=self._inspected,
            on_error=self._failed,
        )

    def _inspected(self, result):
        if self.cancelled:
            self._finish()
            return
        try:
            blocker, optional, empty, prepared = result
            if blocker:
                raise LaunchError("AI 运行条件未满足", blocker)
            self.session.require_current()
            if optional:
                self.config = confirm_optional_retrieval(self.window, self.config, optional)
                if self.config is None or self.cancelled:
                    self.cancel()
                else:
                    self._inspect()
                return
            if (
                empty
                and QMessageBox.question(
                    self.window,
                    "术语库为空",
                    "当前术语来源为空，翻译质量可能下降。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                self.cancel()
                return
            if self.cancelled:
                self._finish()
                return
            self.session.require_current()
            self.request = self.controller.begin(
                self.mode, self.config, prepared.entries, prepared=prepared, **self.options
            )
            self.request = replace(self.request, recovery_directory=self.session.project_dir)
            if self.selection and not self.selection[1]:
                self.request = replace(
                    self.request,
                    reuse_proofread=True,
                    recovery_task_id=None if self.selection[0].get("legacy") else self.selection[0]["task_id"],
                    recovery_record=self.selection[0],
                )
            self.activity = self.controller.create_activity(self.request)
            self.session.bind_run_identity(self.request.run_id)
            self.dialog.setLabelText("正在记录配置与术语版本…")
            request, owner = self.request, self.owner
            self.worker.start(
                lambda: TaskConsistency.capture(request, owner), on_success=self._publish, on_error=self._failed
            )
        except Exception as exc:
            self._failed(str(exc), title=getattr(exc, "title", "AI 任务未启动"))

    def _publish(self, consistency):
        if self.cancelled:
            self._finish()
            return
        try:
            from .task_progress import AiTaskProgressWindow

            self.session.require_current()
            if consistency.error:
                raise RuntimeError(consistency.error)
            if not self.controller.accepts(self.request.run_id):
                self.cancel()
                return
            if self.remote:
                from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

                self.client = ParatranzTermsAPI(self.remote_config)
            self.progress = AiTaskProgressWindow(
                self.request,
                self.session,
                self.activity,
                client=self.client,
                project_id=self.remote["id"] if self.remote else None,
                theme_view=self.window._theme_view,
                consistency=consistency,
            )
            self.window.progress_window_created.emit(self.progress)
            show_and_activate(self.progress)
            self.progress.prepare()
            self._finish(published=True)
            self.window.close()
        except Exception as exc:
            self._failed(str(exc))

    def _failed(self, error, *, title="AI 任务未启动"):
        if not self.cancelled:
            logger.error("AI task startup failed: %s", error)
            if self.progress is not None:
                self.progress._prepare_failed(error)
                self.progress.close()
            elif self.activity is not None:
                self.activity.fail(error)
            QMessageBox.warning(self.window, title, error)
        self._finish()

    def _finish(self, *, published=False):
        if self.finished:
            return
        self.finished = True
        registry = self._recovery_registry
        if self._reservation_task_id is not None and registry is not None:
            registry.release_recovery_launch(self._reservation_task_id, self)
            self._reservation_task_id = None
        if not published:
            if self.request is not None:
                self.controller.cancel(self.request.run_id)
            if self.client is not None and self.progress is None:
                self.client.close()
        if self.request is not None:
            self.controller.finish(self.request.run_id)
        if not isinstance(self.window, QObject) or not sip.isdeleted(self.window):
            self.window._task_launch = None
            refresh = getattr(self.window, "request_task_refresh", None)
            if not published and isinstance(self.window, QWidget) and self.window.isVisible() and refresh is not None:
                refresh()
        if not sip.isdeleted(self.dialog):
            self.dialog.hide()
            self.dialog.deleteLater()
        _ACTIVE_LAUNCHES.discard(self)
        self.deleteLater()
