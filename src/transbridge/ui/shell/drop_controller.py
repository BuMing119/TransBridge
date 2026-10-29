"""Own contextual file-drop choices without mixing them into intent discovery."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QMessageBox

from transbridge.ui.coordinators.source_add_operation import ProjectSourceAddOperation
from transbridge.ui.drop_binding import SafeDropBinding
from transbridge.ui.drop_review import DropReviewDialog
from transbridge.ui.drop_router import DropResolutionStatus

from .action_catalog import IntentId
from .plugin_drop_dialog import PluginDropDialog


class ShellDropController:
    def __init__(self, host, dispatch):
        self._host, self._dispatch = host, dispatch
        self.binding = SafeDropBinding(host, parent=host)
        self.binding.resolution_ready.connect(self.review)
        self.binding.intent_confirmed.connect(dispatch)
        self.review_dialog: DropReviewDialog | None = None
        self.plugin_dialog: PluginDropDialog | None = None
        self._source_add = ProjectSourceAddOperation(host)
        self._closed = False

    def review(self, resolution):
        if self._closed:
            return
        candidate = resolution.candidate
        if candidate is not None and candidate.intent_id is IntentId.PROJECT_CREATE:
            self._plugin(candidate.payload_mapping()["path"])
            return
        if resolution.status not in {
            DropResolutionStatus.CANDIDATE,
            DropResolutionStatus.REJECTED,
            DropResolutionStatus.NEEDS_CHOICE,
        }:
            return
        if self.review_dialog is None:
            self.review_dialog = DropReviewDialog(self._host)
            self.review_dialog.confirm_requested.connect(self.binding.confirm)
            self.review_dialog.dismiss_requested.connect(self.binding.dismiss)
        self.review_dialog.review(resolution)
        self.review_dialog.exec()

    def _busy(self):
        context = self._host.context
        if context.project_opening or context.project_view_failed:
            self._host.show_message("当前工程正在加载或需要恢复，请处理后重新拖入插件。", error=True)
            return True
        if any(
            getattr(self._host, name, None) is not None
            for name in ("foreground_worker", "save_worker", "project_open_worker")
        ):
            self._host.show_message("另一项后台操作仍在进行，请完成后重新拖入插件。")
            return True
        return False

    def _plugin(self, path):
        if self._busy():
            return
        context = self._host.context
        if not context.project_name:
            self._dispatch(IntentId.PROJECT_CREATE, {"path": path})
            return
        identity = context.active_version_identity
        project_name = context.project_name
        reason = ""
        if identity is None or not context.uses_authoritative_projection:
            reason = "当前项目没有可编辑的工程版本，无法加入插件。"
        dialog = PluginDropDialog(path, project_name, add_unavailable=reason, parent=self._host)
        self.plugin_dialog = dialog
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            choice = dialog.choice
        finally:
            self.plugin_dialog = None
            dialog.deleteLater()
        if not accepted or self._closed or self._busy():
            return
        if (context.active_version_identity, context.project_name) != (identity, project_name):
            self._host.show_message("目标项目已变化，请重新拖入插件。", error=True)
            return
        if choice == "add":
            self._source_add.start(path, identity)
        elif choice == "new":
            self._new_project(path, identity)

    def _new_project(self, path, identity):
        context = self._host.context

        def continue_creation(saved):
            if self._closed or not saved:
                return
            if context.active_version_identity != identity:
                self._host.show_message("当前工程版本已变化，请重新拖入插件。", error=True)
                return
            self._dispatch(IntentId.PROJECT_CREATE, {"path": path})

        if not context.dirty and not context.authoritative_projection_diverged():
            continue_creation(True)
            return
        answer = QMessageBox.question(
            self._host,
            "保存当前项目",
            "当前项目有未保存修改。保存成功后继续新建项目？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save and not self._closed:
            if context.active_version_identity != identity:
                self._host.show_message("当前工程版本已变化，请重新拖入插件。", error=True)
                return
            self._host.save_current_project_async(on_finished=continue_creation)

    def close(self):
        self._closed = True
        self.binding.close()
        if self.review_dialog is not None:
            self.review_dialog.close()
        if self.plugin_dialog is not None:
            self.plugin_dialog.reject()
