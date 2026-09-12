"""Project terminology summary displayed above legacy term-source settings."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from transbridge.ui.foundation.components import ElidedLabel

from .task_widget_style import configure_task_button, configure_task_panel


class ProjectTerminologyPanel(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("当前项目术语库", parent)
        configure_task_panel(self)
        layout = QVBoxLayout(self)
        self.context_label = ElidedLabel("", self)
        self.context_label.setAccessibleName("项目术语所属工程与翻译版本")
        layout.addWidget(self.context_label)
        self.status_label = QLabel("尚未读取项目术语库。", self)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("当前项目术语库状态")
        layout.addWidget(self.status_label)
        self.version_label = ElidedLabel("", self)
        self.version_label.setAccessibleName("当前项目术语版本")
        layout.addWidget(self.version_label)
        hint = QLabel(
            "任务开始时固定当前已发布版本，并应用上方译名方案；术语按插件范围和文本匹配使用。",
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.open_button = QPushButton("查看项目术语库…", self)
        self.open_button.setAccessibleName("查看当前项目术语库")
        self.open_button.setToolTip("打开项目术语工作台，查看术语条目、发布版本并管理译名方案")
        configure_task_button(self.open_button)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.setAccessibleName("刷新当前项目术语库状态")
        configure_task_button(self.refresh_button)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)

    def render(self, status: str, version: str = "") -> None:
        self.status_label.setText(status)
        self.status_label.setToolTip(status)
        self.status_label.setAccessibleDescription(status)
        self.version_label.set_full_text(version)
        self.version_label.setToolTip(version)
        self.version_label.setVisible(bool(version))

    def set_context(self, text: str) -> None:
        self.context_label.set_full_text(text)
        self.context_label.setToolTip(text)


__all__ = ["ProjectTerminologyPanel"]
