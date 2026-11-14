# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Own one project encoding change from draft through background publication."""

from dataclasses import replace
from threading import Event
from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QMessageBox, QProgressDialog

from transbridge.application.contracts import DomainError
from transbridge.ui.project_text_encoding_dialog import ProjectTextEncodingDialog


class ProjectTextEncodingCoordinator:
    def __init__(self, host) -> None:
        self._host = host
        self._busy = False

    def availability(self) -> tuple[bool, str | None]:
        context = self._host.context
        if self._busy:
            return False, "项目编码变更正在进行。"
        if not getattr(context, "active_project_id", None):
            return False, "请先打开本地翻译工程。"
        if getattr(context, "project_text_encodings", None) is None:
            return False, "当前工程的文本编码设置不可用。"
        if context.project_opening or context.project_view_failed:
            return False, "请先完成工程打开。"
        return True, None

    def start(self) -> None:
        available, reason = self.availability()
        if not available:
            self._host.show_message(reason)
            return
        context = self._host.context
        service = context.project_text_encodings
        request = replace(
            self._host.runtime_context,
            project_id=context.active_project_id,
            variant_id=context.active_variant_id,
            run_id=f"text-encoding-{uuid4().hex}",
        )
        try:
            path = service.active_project_path(request)
            current = service.active_encodings(request)
        except (DomainError, OSError, RuntimeError, ValueError) as exc:
            self._error(str(exc))
            return
        if not path or current is None:
            self._error("未找到活动工程，请重新打开工程后重试。")
            return
        self._busy = True
        dialog = ProjectTextEncodingDialog(current, self._host)
        try:
            encodings = dialog.fields.value() if dialog.exec() == QDialog.DialogCode.Accepted else current
        finally:
            dialog.deleteLater()
        if encodings == current:
            self._busy = False
            return
        self._prepare(service, path, request, encodings)

    def _prepare(self, service, path, request, encodings) -> None:
        def completed(result, cancelled):
            if cancelled:
                if result.is_success and result.value is not None:
                    self._discard(service, result.value.token, request)
                else:
                    self._busy = False
                    self._host.show_message("已取消项目编码变更。")
                return
            if result.is_success and result.value is not None:
                self._commit(service, request, result.value)
                return
            self._error(self._diagnostics(result))

        self._dispatch(
            lambda cancelled: service.prepare(path, encodings, request, cancelled=cancelled),
            "正在准备项目编码变更…",
            completed,
        )

    def _commit(self, service, request, preview) -> None:
        self._dispatch(
            lambda cancelled: service.commit(preview.token, request, cancelled=cancelled),
            "正在保存项目文本编码…",
            lambda result, cancelled: self._finish(service, request, preview, result, cancelled),
            on_failure=lambda error=None: self._discard(service, preview.token, request, error=error),
        )

    def _finish(self, service, request, preview, result, cancelled) -> None:
        if not result.is_success or result.value is None:
            self._discard(service, preview.token, request, error=None if cancelled else self._diagnostics(result))
            return
        self._busy = False
        self._host.show_message("项目语言和编码设置已保存。已有原文保持不变，可通过“更新来源”应用新的读取规则。")

    def _discard(self, service, token, request, *, error=None) -> None:
        service.discard(token, request)
        if error:
            self._error(error)
        else:
            self._busy = False
            self._host.show_message("已取消项目编码变更。")

    def _dispatch(self, operation, message, callback, *, on_failure=None) -> None:
        cancelled = Event()
        progress = QProgressDialog(message, "取消", 0, 0, self._host)
        progress.setWindowTitle("项目文本编码")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(cancelled.set)

        def finish(result):
            stopped = cancelled.is_set()
            progress.reset()
            progress.deleteLater()
            callback(result, stopped)

        def fail(error):
            progress.reset()
            progress.deleteLater()
            (on_failure or self._error)(error)

        if self._host.start_foreground_task(
            lambda: operation(cancelled.is_set),
            message=message,
            on_result=finish,
            on_error=fail,
            before_start=progress.show,
        ):
            return
        progress.deleteLater()
        self._busy = False
        if on_failure is not None:
            on_failure()

    @staticmethod
    def _diagnostics(result) -> str:
        return "\n".join(f"{item.code}: {item.message}" for item in result.diagnostics) or ("无法保存项目文本编码。")

    def _error(self, message) -> None:
        self._busy = False
        self._host.show_message(message, error=True)
        QMessageBox.warning(self._host, "项目文本编码变更未完成", message)
