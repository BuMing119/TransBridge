"""Choose the destination of a plugin dropped into an existing project."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, configure_dialog


class PluginDropDialog(QDialog):
    def __init__(self, path: str, project_name: str, *, add_unavailable: str = "", parent=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("选择插件的目标项目")
        self.setAccessibleName("插件拖放项目选择")
        self.choice: str | None = None
        layout = QVBoxLayout(self)
        self.summary = QLabel(f"插件：{path}\n当前项目：{project_name}", self)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.notice = QLabel(add_unavailable or "选择将插件加入当前项目，或用它创建一个新项目。", self)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(self)
        self.add_button = buttons.addButton("加入当前项目", QDialogButtonBox.ButtonRole.ActionRole)
        self.new_button = buttons.addButton("新建项目", QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.add_button.setAccessibleName(f"加入当前项目：{project_name}")
        self.add_button.setEnabled(not add_unavailable)
        self.add_button.setToolTip(add_unavailable)
        self.add_button.clicked.connect(lambda: self._choose("add"))
        self.new_button.clicked.connect(lambda: self._choose("new"))
        buttons.rejected.connect(self.reject)
        for button in (self.add_button, self.new_button, self.cancel_button):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        primary = self.new_button if add_unavailable else self.add_button
        primary.setDefault(True)
        primary.setFocus()
        layout.addWidget(buttons)

    def _choose(self, choice: str) -> None:
        if choice == "add" and not self.add_button.isEnabled():
            return
        self.choice = choice
        self.accept()
