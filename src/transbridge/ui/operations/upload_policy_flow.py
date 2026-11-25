# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Background policy I/O and the Owner's two entry points into the same editor."""

from threading import Event

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout

from transbridge.application.upload_policy import ProjectRole
from transbridge.paratranz.project_policy import default_upload_policy
from transbridge.ui.foundation.components import configure_dialog
from transbridge.ui.workers import ApiWorker

from .upload_policy_editor import UploadPolicyEditor


class PolicyRequest(QDialog):
    def __init__(self, session, parent, **options):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("项目上传规则")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("正在保存到 ParaTranz…" if options else "正在读取项目上传规则…"))
        progress = QProgressBar(self)
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.access = None
        self.error = ""
        cancel = Event()
        self.worker = ApiWorker(
            session.policy_access, cancel, **options, route_http_errors=False, cancel_read_on_close=cancel.set
        )
        self.worker.result.connect(self._result)
        self.worker.error.connect(self._error)
        self.worker.finished.connect(self.accept)
        self.rejected.connect(cancel.set)
        QTimer.singleShot(0, self.worker.start)

    def _result(self, access):
        self.access = access

    def _error(self, message):
        self.error = message


def _request(session, parent, **options):
    while True:
        dialog = PolicyRequest(session, parent, **options)
        outcome = dialog.exec()
        error, access = dialog.error, dialog.access
        dialog.deleteLater()
        if outcome != QDialog.DialogCode.Accepted:
            return None
        session.check_policy_target()
        if access is not None:
            return access
        choice = QMessageBox.warning(
            parent,
            "项目上传规则",
            error or "未收到云端结果。",
            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Retry:
            return None


def edit_policy(session, access, parent):
    editor = UploadPolicyEditor(access, parent)
    try:
        while editor.exec() == QDialog.DialogCode.Accepted:
            saved = _request(session, editor.parentWidget(), previous=access, policy=editor.policy())
            if saved is not None:
                return saved
        return None
    finally:
        editor.deleteLater()


def initial_policy(session, parent):
    access = _request(session, parent)
    if access is None or access.policy is not None or access.role != ProjectRole.OWNER:
        return access
    prompt = QMessageBox(parent)
    prompt.setWindowTitle("项目上传规则")
    prompt.setText("要为这个项目设置上传规则吗？")
    prompt.setInformativeText("你可以为你的项目配置更加自定义的上传规则，以约束成员的上传行为")
    use_default = prompt.addButton("否，使用默认权限并继续", QMessageBox.ButtonRole.AcceptRole)
    customize = prompt.addButton("设置上传规则", QMessageBox.ButtonRole.ActionRole)
    cancel = prompt.addButton(QMessageBox.StandardButton.Cancel)
    cancel.setText("取消")
    prompt.setEscapeButton(cancel)
    prompt.exec()
    clicked = prompt.clickedButton()
    prompt.deleteLater()
    if clicked is use_default:
        saved = _request(session, parent, previous=access, policy=default_upload_policy())
        if saved is not None:
            notice = QMessageBox(parent)
            notice.setIcon(QMessageBox.Icon.Information)
            notice.setWindowTitle("上传规则已保存")
            notice.setText("默认上传规则已保存到当前 ParaTranz 项目。")
            notice.setInformativeText("以后可以在上传窗口中点「编辑项目政策」修改。")
            notice.addButton("继续", QMessageBox.ButtonRole.AcceptRole)
            notice.exec()
            notice.deleteLater()
        return saved
    if clicked is customize:
        return edit_policy(session, access, parent)
    return None
