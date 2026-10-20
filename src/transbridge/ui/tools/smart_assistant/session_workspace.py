"""Match the viewed conversation to the installed workbench before execution."""

from dataclasses import replace
import logging

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QMessageBox

from .session_scope_notice import SessionScopeNotice

logger = logging.getLogger(__name__)


class SessionWorkspace(QObject):
    def __init__(self, panel, context):
        super().__init__(panel)
        self.panel = panel
        self.context = context
        self.switching = False
        self.suspended = False
        self.banner = SessionScopeNotice(theme=getattr(panel, "_theme", None))
        for name in ("project_installed", "project_changed", "variant_changed"):
            signal = getattr(context, name, None)
            if signal is not None:
                signal.connect(self.changed)

    def identity(self):
        identity = getattr(self.context, "active_version_identity", None)
        return (None, None) if identity is None else tuple(identity)

    def current_context(self):
        context = self.panel._runtime_context
        if context is None:
            return None
        project_id, variant_id = self.identity()
        return replace(context, project_id=project_id, variant_id=variant_id, session_id=None)

    def bound_context(self, values=None):
        values = self.panel._sessions.values if values is None else values
        context = self.panel._runtime_context
        if context is None:
            return None
        return replace(
            context,
            project_id=values.get("project_id"),
            variant_id=values.get("variant_id"),
            session_id=values.get("session_id"),
        )

    def matches(self, values=None):
        values = self.panel._sessions.values if values is None else values
        if self.workbench_issue():
            return False
        return (values.get("project_id"), values.get("variant_id")) == self.identity()

    def workbench_issue(self):
        if getattr(self.context, "project_opening", False):
            return "正在加载工程与版本，请等待加载完成后重试。"
        if getattr(self.context, "project_view_failed", False) or getattr(
            self.context, "authoritative_projection_diverged", False
        ):
            return "工作台尚未完整加载工程，请重新打开工程后重试。"
        return ""

    def ready(self):
        sessions = self.panel._sessions
        return (
            not self.panel._disposed
            and not self.switching
            and not sessions.loading
            and not sessions.preview
            and bool(sessions.values.get("session_id"))
            and self.matches()
        )

    def labels(self, values):
        directory_labels = getattr(self.panel._sessions, "directory_labels", None)
        if directory_labels is not None:
            return directory_labels(values)
        lookup = self.panel._navigation.get("session_project_labels")
        if lookup is not None:
            return lookup(values.get("project_id"), values.get("variant_id"))
        return {
            "project_name": values.get("project_id") or "未归属对话",
            "variant_name": values.get("variant_id") or "未绑定版本",
            "available": bool(values.get("project_id") and values.get("variant_id")),
        }

    def ensure(self, action, resume):
        panel, sessions = self.panel, self.panel._sessions
        if panel._disposed or self.switching or sessions.loading:
            return False
        if self.ready():
            self.resume_execution()
            return True
        if self.matches():
            generation, target = sessions.generation, self.bound_context()

            def continued():
                if self.ready() and generation == sessions.generation and target == self.bound_context():
                    resume()

            if sessions.activate_view():
                QTimer.singleShot(0, continued)
            return False
        if issue := self.workbench_issue():
            QMessageBox.warning(panel, "工作台尚未就绪", issue)
            return False
        target = self.bound_context()
        labels = self.labels(sessions.values)
        if target is None or not target.project_id or not target.variant_id or not labels.get("available", True):
            QMessageBox.warning(
                panel,
                "当前对话仅供浏览",
                labels.get("reason") or "此对话没有完整的工程与版本绑定。请在目标工程中新建对话。",
            )
            return False
        switch = panel._navigation.get("switch_project")
        if switch is None:
            QMessageBox.warning(panel, "无法切换工程", "当前窗口没有工程切换服务，请打开对应工程与版本后重试。")
            return False
        generation = sessions.generation
        if not self.confirm_switch(action, labels):
            return False
        if generation != sessions.generation or panel._disposed:
            return False
        self.switching = True
        self.refresh_banner()
        completed = False

        def finished(success):
            nonlocal completed
            if completed:
                return
            completed = True
            self.switching = False
            if panel._disposed:
                return
            self.refresh_banner()
            if generation != sessions.generation or target != self.bound_context():
                return
            if success and self.matches() and sessions.activate_view():
                resume()

        try:
            switch(target, finished)
        except Exception:
            logger.exception("Could not switch to the conversation's workspace")
            finished(False)
            if not panel._disposed:
                QMessageBox.warning(panel, "切换工程失败", "切换服务发生错误，内容尚未发送。请检查工程状态后重试。")
        return False

    def confirm_switch(self, action, labels):
        dialog = QMessageBox(self.panel)
        same_project = self.panel._sessions.values.get("project_id") == self.identity()[0]
        target = "版本" if same_project else "工程与版本"
        dialog.setWindowTitle(f"切换到对话所属{target}")
        dialog.setText(
            f"此对话属于「{labels['project_name']} / {labels['variant_name']}」。\n"
            f"需要先切换到该{target}，才能{action}。是否切换？"
        )
        accept = dialog.addButton(f"切换并{action}", QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        return dialog.clickedButton() is accept

    def changed(self, *_):
        if self.panel._disposed:
            return
        self.panel._refresh_session_list()
        binding = getattr(self.panel._chat, "_request_binding", None)
        if binding is not None and not self.matches():
            self.suspended = self.suspended or binding._active
            binding.set_active(False)

    def resume_execution(self):
        if not self.suspended or not self.ready():
            return
        self.suspended = False
        binding = getattr(self.panel._chat, "_request_binding", None)
        if binding is not None:
            binding.set_active(True)

    def refresh_banner(self):
        sessions = self.panel._sessions
        if self.switching:
            self.banner.show_notice(
                "正在切换", "工程与版本加载完成后，将继续本次操作。", icon="clock-hour-3", kind="switching"
            )
        elif sessions.loading:
            self.banner.show_notice("正在读取", "正在加载对话历史…", icon="clock-hour-3", kind="loading")
        elif sessions.values and (issue := self.workbench_issue()):
            opening = getattr(self.context, "project_opening", False)
            self.banner.show_notice(
                "工程加载中" if opening else "工程尚未就绪",
                issue,
                state="info" if opening else "warning",
                icon="clock-hour-3" if opening else "alert-triangle",
                kind="workbench-pending",
            )
        elif sessions.values and not self.matches():
            labels = self.labels(sessions.values)
            if labels.get("available"):
                same_project = sessions.values.get("project_id") == self.identity()[0]
                target = "版本" if same_project else "工程"
                self.banner.show_notice(
                    f"其他{target} · 仅浏览",
                    f"对话属于「{labels['project_name']} / {labels['variant_name']}」。"
                    f"发送或继续操作时，需确认切换{target}。",
                    kind="other-variant" if same_project else "other-project",
                )
            else:
                self.banner.show_notice(
                    "仅供浏览",
                    labels.get("reason") or "此对话未绑定可用的工程与版本。请在目标工程中新建对话。",
                    state="muted",
                    kind="unavailable",
                )
        else:
            # A detached preview can already match the workbench. Displaying it
            # does not activate or resume it; the explicit execution guard does.
            self.banner.clear()

    def apply_theme(self, theme):
        self.banner.apply_theme(theme)
