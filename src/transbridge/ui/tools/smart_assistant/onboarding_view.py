"""Readiness-specific empty state using real navigation callbacks supplied by the shell."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .quick_actions import QuickActionsChips
from .theme_support import BUTTON_STRUCTURE_STYLE, SmartAssistantTheme


class OnboardingView(QWidget):
    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._open_settings = self._open_project = None
        self._state = (False, "", 0, "")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 20, 12, 20)
        layout.setSpacing(10)
        self.title = QLabel()
        self.description = QLabel()
        for label in (self.title, self.description):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.configure_button = QPushButton("配置 AI 服务")
        self.open_project_button = QPushButton("打开项目")
        for button in (self.configure_button, self.open_project_button):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.configure_button.clicked.connect(self._configure)
        self.open_project_button.clicked.connect(self._open)
        self.quick = QuickActionsChips(theme=self._theme)
        layout.addWidget(self.quick)
        self.apply_theme(self._theme)
        self.set_state(model_configured=False, project_name="", selected_count=0)

    def configure_navigation(self, *, open_settings=None, open_project=None):
        self._open_settings, self._open_project = open_settings, open_project
        self.set_state(
            model_configured=self._state[0],
            project_name=self._state[1],
            selected_count=self._state[2],
            service_message=self._state[3],
        )

    def set_state(self, *, model_configured: bool, project_name: str, selected_count: int, service_message: str = ""):
        self._state = (model_configured, project_name, selected_count, service_message)
        if not model_configured:
            title = "先配置 AI 服务"
            description = service_message or "配置模型、服务地址和 API Key 后，可以提问或处理项目中的词条。"
            if self._open_settings is None:
                description += "请从主窗口的设置 → AI 服务完成配置。"
        elif not project_name:
            title = "从提问开始，或打开一个项目"
            description = "可以先进行一般问答或参考文件解读；翻译、质量检查和导出需要打开项目。"
            if self._open_project is None:
                description += "请从主窗口打开项目。"
        elif not selected_count:
            title = f"已打开 {project_name}"
            description = "可以提问或检查项目。若要翻译选中的内容，请先在工作台选择词条。"
        else:
            title = f"准备处理选中的 {selected_count} 条词条"
            description = "发送时会保存本次范围；之后改变工作台选区，不会改写已提交任务的范围。"
        self.title.setText(title)
        self.description.setText(description)
        self.configure_button.setVisible(not model_configured and self._open_settings is not None)
        self.open_project_button.setVisible(not project_name and self._open_project is not None)
        self.quick.setEnabled(model_configured)
        self.quick.set_context(project_available=bool(project_name), selected_count=selected_count)

    def _configure(self):
        if self._open_settings is not None:
            self._open_settings()

    def _open(self):
        if self._open_project is not None:
            self._open_project()

    def apply_theme(self, theme):
        self._theme = theme
        theme.apply_surface(self)
        theme.apply_semantic(self.title, "default")
        theme.apply_semantic(self.description, "muted")
        theme.apply_accent(self.configure_button)
        theme.apply_surface(self.open_project_button)
        self.quick.apply_theme(theme)
