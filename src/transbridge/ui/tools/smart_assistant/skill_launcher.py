"""Skill selection previews an editable request; only explicit launch submits it."""

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from transbridge.smart_assistant.skills import SkillExecutor, SkillRegistry

from .submission_scope import capture_display_context
from .theme_support import BUTTON_STRUCTURE_STYLE, SmartAssistantTheme


class SkillLaunchDialog(QDialog):
    def __init__(self, spec, *, submit, scope, parent=None, theme=None):
        super().__init__(parent)
        self._submit, self._scope = submit, scope
        self._launching = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"启动技能 · {spec.display_name}")
        self.resize(520, 390)
        layout = QVBoxLayout(self)
        self.description = QLabel(spec.description or "该技能尚未提供说明，请确认适用范围后填写要求。")
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.scope_label = QLabel()
        self.scope_label.setTextFormat(Qt.TextFormat.PlainText)
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        label = QLabel("本次要求（可以修改）")
        layout.addWidget(label)
        self.request = QTextEdit()
        self.request.setAccessibleName("技能的本次要求")
        self.request.setPlainText(spec.description)
        layout.addWidget(self.request, 1)
        self.notice = QLabel("点击启动后发送；仍遵守当前执行模式和权限确认。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        self.run_button = QPushButton("启动技能")
        self.run_button.clicked.connect(self._run)
        self.request.textChanged.connect(self._update_run)
        actions.addStretch(1)
        for button in (self.cancel_button, self.run_button):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            actions.addWidget(button)
        layout.addLayout(actions)
        theme = theme or SmartAssistantTheme()
        theme.apply_surface(self)
        theme.apply_surface(self.request)
        theme.apply_semantic(self.description, "default")
        theme.apply_semantic(self.scope_label, "muted")
        theme.apply_semantic(self.notice, "muted")
        theme.apply_accent(self.run_button)
        self._update_run()
        self._refresh_scope()

    def _update_run(self):
        self.run_button.setEnabled(not self._launching and bool(self.request.toPlainText().strip()))

    def _run(self):
        request = self.request.toPlainText().strip()
        if self._launching or not request:
            return
        self._launching = True
        self._update_run()
        if self._submit(request):
            self.accept()
        else:
            self.notice.setText("暂未发送。请处理会话中的提示后重试，本次要求已保留。")
            self._launching = False
            self._update_run()

    def _refresh_scope(self):
        self.scope_label.setText(f"启动时使用当前范围：{self._scope()}。切换会话会关闭此预览。")

    def event(self, event):
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "scope_label"):
            self._refresh_scope()
        return super().event(event)


class SkillLauncher(QObject):
    def __init__(self, facade):
        super().__init__(facade)
        self._facade = facade
        self.dialog = None

    def open(self, skill_name):
        spec = SkillRegistry.get(skill_name)
        if spec is None or not spec.enabled:
            self._facade.add_system_message("该技能当前不可用，请重新选择。")
            return
        self.close()
        self.dialog = SkillLaunchDialog(
            spec,
            submit=lambda request: SkillExecutor(self._facade).execute(spec, user_request=request),
            scope=self._scope_summary,
            parent=self._facade,
            theme=getattr(self._facade, "_theme", None),
        )
        self.dialog.finished.connect(self._finished)
        self.dialog.show()
        self.dialog.raise_()

    def _scope_summary(self):
        context = self._facade.context
        project = capture_display_context(context)["project_name"] or "未打开项目"
        selected = getattr(context, "selected_entries", None)
        if selected is None:
            selected = getattr(context, "selected_ids", ())
        return f"{project} · 选中 {len(selected or ())} 条"

    @pyqtSlot(int)
    def _finished(self, _result):
        self.dialog = None

    def close(self):
        if self.dialog is not None:
            self.dialog.close()
