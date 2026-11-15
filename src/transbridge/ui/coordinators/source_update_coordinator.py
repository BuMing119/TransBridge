# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose, preview and confirm source replacement without blocking the Qt loop."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtWidgets import QMessageBox

from transbridge.application.projects.source_input_candidates import collect_input_files
from transbridge.ui.source_update_dialog import SourceUpdateDialog


class SourceUpdateCoordinator:
    def __init__(self, host) -> None:
        self._host = host
        self._busy = False
        self._dialog: SourceUpdateDialog | None = None
        self._preview_token: str | None = None

    def start_current(self) -> None:
        if not self._available():
            return
        context = self._context(self._host.context.active_project_id)
        project_path = self._service().active_project_path(context)
        if not project_path:
            self._host.show_message("请先打开本地翻译工程。")
            return
        name = getattr(self._host.context, "project_name", None)
        self._start(project_path, name or context.project_id, context, recovery=False)

    def start_recovery(self, recovery) -> None:
        if not self._available():
            return
        context = self._context(recovery.variant.ref.project_id.value)
        self._start(recovery.project_path, getattr(recovery, "name", context.project_id), context, recovery=True)

    def _available(self) -> bool:
        if getattr(self._host.context, "project_view_failed", False):
            self._host.show_message("工作台未能加载，请重新打开工程后再更新源文件。")
            return False
        if getattr(self._host.context, "project_opening", False):
            self._host.show_message("项目仍在打开，请稍候再更新源文件。")
            return False
        if self._busy:
            self._host.show_message("源文件更新正在进行，请先完成或取消当前预览。")
            return False
        runtime = self._host.app_runtime
        if runtime is None or "project_source_updates" not in runtime.use_cases.names():
            self._host.show_message("当前工程不支持更新源文件。")
            return False
        return True

    def _context(self, project_id):
        return replace(
            self._host.runtime_context,
            project_id=project_id,
            variant_id=None,
            run_id=f"source-update-{uuid4().hex}",
        )

    def _service(self):
        return self._host.app_runtime.use_cases.resolve("project_source_updates")

    def _start(self, path, name, context, *, recovery: bool) -> None:
        self._busy = True
        self._dispatch(
            lambda: self._service().list_sources(path, context),
            "正在读取工程来源…",
            lambda result: self._choose(result, path, name, context, recovery=recovery),
        )

    def _choose(self, result, path, name, context, *, recovery: bool) -> None:
        if not self._check(result):
            return
        sources = result.value
        if not sources:
            self._busy = False
            self._host.show_message("此工程没有可更新的源文件。")
            return
        dialog = SourceUpdateDialog(sources, name or path, self._host)
        self._dialog = dialog
        dialog.discovery_requested.connect(self._discover)
        dialog.preview_requested.connect(lambda pairs: self._prepare(path, pairs, context, recovery=recovery))
        dialog.back_requested.connect(lambda token: self._back(token, context))
        dialog.commit_requested.connect(lambda token: self._commit(token, context, recovery=recovery))
        dialog.rejected.connect(lambda: self._close(context))
        dialog.open()

    def _discover(self, paths, directory) -> None:
        dialog = self._dialog
        dialog.set_busy(True, "正在查找新版文件…")
        self._dispatch(
            lambda: collect_input_files(paths, directory=directory),
            "正在查找新版文件…",
            dialog.show_discovery,
        )

    def _prepare(self, path, pairs, context, *, recovery: bool) -> None:
        self._dialog.set_busy(True, "正在准备差异预览…")

        def prepare(saved=True):
            if not saved:
                self._error("工程尚未保存。请先完成保存，再预览差异。")
                return
            self._dispatch(
                lambda: self._service().prepare_batch(path, pairs, context),
                "正在比较所选来源和全部翻译版本…",
                self._preview,
            )

        if recovery:
            prepare()
        elif self._host.save_current_project_async(on_finished=prepare) is False:
            prepare(False)

    def _preview(self, result) -> None:
        if not self._check(result):
            return
        self._preview_token = result.value.token
        self._dialog.show_preview(result.value)

    def _back(self, token, context) -> None:
        self._service().discard(token, context)
        self._preview_token = None
        self._dialog.show_pairing()

    def _commit(self, token, context, *, recovery: bool) -> None:
        self._dialog.set_busy(True, "正在备份工程并更新全部翻译版本…")
        workbench_context = self._host.context
        view_token = None

        def begin():
            nonlocal view_token
            if workbench_context.active_project_id == context.project_id:
                view_token = workbench_context.begin_project_open()

        def finish_view():
            if view_token is not None:
                workbench_context.finish_project_open(view_token)

        def failed(message):
            finish_view()
            self._service().discard(token, context)
            self._preview_token = None
            self._host.show_message(message)
            self._dialog.show_pairing(message)

        def finished(result):
            if self._check(result, on_error=failed):
                self._preview_token = None
                if getattr(result.value, "active_token", None) is None:
                    finish_view()
                self._finish(result.value, context, recovery=recovery, view_token=view_token)

        self._dispatch(
            lambda: self._service().commit(token, context),
            "正在备份工程并更新全部翻译版本…",
            finished,
            on_error=failed,
            before_start=begin if hasattr(workbench_context, "begin_project_open") else None,
        )

    def _close(self, context, *, accepted: bool = False) -> None:
        dialog, self._dialog = self._dialog, None
        token, self._preview_token = self._preview_token, None
        if token is not None:
            self._service().discard(token, context)
        self._busy = False
        if dialog is not None:
            if accepted:
                dialog.accept()
            dialog.deleteLater()

    def _finish(self, value, context, *, recovery: bool, view_token=None) -> None:
        self._close(context, accepted=True)
        self._host.show_message(f"源文件已更新，全部版本已迁移。更新前备份：{value.backup_path}")
        active_id = self._host.context.active_project_id
        if (token := getattr(value, "active_token", None)) is not None:
            self._host.project_coordinator.refresh_committed_sources(token, context, view_token=view_token)
        elif not recovery or active_id in (None, context.project_id):
            self._host.project_coordinator.open_project_path(value.project_path)
        else:
            QMessageBox.information(
                self._host,
                "源文件已更新",
                f"全部翻译版本已迁移。\n更新前备份：{value.backup_path}\n"
                "可在恢复窗口点击“重新检查来源”打开更新后的工程。",
            )

    def _dispatch(self, operation, message, callback, *, on_error=None, before_start=None) -> None:
        fail = on_error or self._error
        started = self._host.start_foreground_task(
            operation,
            message=message,
            on_result=callback,
            on_error=fail,
            before_start=before_start,
            disable_workbench=self._dialog is None,
        )
        if not started:
            fail("另一项操作仍在进行，请稍后重试。")

    def _check(self, result, *, on_error=None) -> bool:
        if result.is_success and result.value is not None:
            return True
        (on_error or self._error)("\n".join(f"{item.code}: {item.message}" for item in result.diagnostics))
        return False

    def _error(self, message: str) -> None:
        self._host.show_message(message)
        if self._dialog is not None:
            self._dialog.set_busy(False, message)
            return
        self._busy = False
        QMessageBox.warning(self._host, "源文件更新未完成", message)


__all__ = ["SourceUpdateCoordinator"]
