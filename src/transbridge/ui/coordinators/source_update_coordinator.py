"""Choose, preview and confirm source replacement without blocking the Qt loop."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PyQt6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from transbridge.ui.source_update_preview import SourceUpdatePreviewDialog


class SourceUpdateCoordinator:
    def __init__(self, host) -> None:
        self._host = host
        self._busy = False

    def start_current(self) -> None:
        if not self._available():
            return
        context = self._context(self._host.context.active_project_id)
        project_path = self._service().active_project_path(context)
        if not project_path:
            self._host.show_message("请先打开本地翻译工程。")
            return
        self._start(project_path, context, recovery=False)

    def start_recovery(self, recovery) -> None:
        if not self._available():
            return
        context = self._context(recovery.variant.ref.project_id.value)
        self._start(recovery.project_path, context, recovery=True)

    def _available(self) -> bool:
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

    def _start(self, path, context, *, recovery: bool) -> None:
        self._busy = True
        self._dispatch(
            lambda: self._service().list_sources(path, context),
            "正在读取工程来源…",
            lambda result: self._choose(result, path, context, recovery=recovery),
        )

    def _choose(self, result, path, context, *, recovery: bool) -> None:
        if not self._check(result):
            return
        sources = result.value
        if not sources:
            self._busy = False
            self._host.show_message("此工程没有可更新的源文件。")
            return
        labels = [f"{index + 1}. {item.label} — {item.location}" for index, item in enumerate(sources)]
        choice, accepted = QInputDialog.getItem(self._host, "更新源文件", "选择要更新的来源：", labels, 0, False)
        if not accepted:
            self._busy = False
            return
        source = sources[labels.index(choice)]
        filters = {
            "plugin.sse": "插件 (*.esp *.esm *.esl)",
            "xml.eet": "ESP-ESM Translator XML (*.xml)",
            "xml.xt": "XTranslator XML (*.xml)",
        }
        replacement, _ = QFileDialog.getOpenFileName(
            self._host,
            "选择新版源文件",
            str(Path(source.location).parent),
            filters.get(str(source.format_id), "源文件 (*)") + ";;所有文件 (*)",
        )
        if not replacement:
            self._busy = False
            return

        def prepare(saved=True):
            if not saved:
                self._busy = False
                return
            self._dispatch(
                lambda: self._service().prepare(path, source.source_id, replacement, context),
                "正在解析新版来源并比较全部翻译版本…",
                lambda prepared: self._preview(prepared, context, recovery=recovery),
            )

        if not recovery:
            self._host.save_current_project_async(on_finished=prepare)
        else:
            prepare()

    def _preview(self, result, context, *, recovery: bool) -> None:
        if not self._check(result):
            return
        preview = result.value
        dialog = SourceUpdatePreviewDialog(preview, self._host)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted:
            self._dispatch(
                lambda: self._service().discard(preview.token, context),
                "正在取消源文件更新…",
                lambda _result: setattr(self, "_busy", False),
            )
            return
        self._dispatch(
            lambda: self._service().commit(preview.token, context),
            "正在备份工程并更新全部翻译版本…",
            lambda committed: self._finish(committed, context, recovery=recovery),
        )

    def _finish(self, result, context, *, recovery: bool) -> None:
        if not self._check(result):
            return
        self._busy = False
        value = result.value
        self._host.show_message(f"源文件已更新，全部版本已迁移。更新前备份：{value.backup_path}")
        active_id = self._host.context.active_project_id
        if not recovery or active_id in (None, context.project_id):
            self._host.project_coordinator.open_project_path(value.project_path)
        else:
            QMessageBox.information(
                self._host,
                "源文件已更新",
                f"全部翻译版本已迁移。\n更新前备份：{value.backup_path}\n"
                "可在恢复窗口点击“重新检查来源”打开更新后的工程。",
            )

    def _dispatch(self, operation, message, callback) -> None:
        started = self._host.start_foreground_task(operation, message=message, on_result=callback, on_error=self._error)
        if not started:
            self._busy = False

    def _check(self, result) -> bool:
        if result.is_success and result.value is not None:
            return True
        self._error("\n".join(f"{item.code}: {item.message}" for item in result.diagnostics))
        return False

    def _error(self, message: str) -> None:
        self._busy = False
        self._host.show_message(message)
        QMessageBox.warning(self._host, "源文件更新未完成", message)


__all__ = ["SourceUpdateCoordinator"]
