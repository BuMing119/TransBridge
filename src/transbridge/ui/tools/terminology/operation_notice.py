"""Persistent, selectable operation feedback shared by every workbench page."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .build_view import TechnicalDetailsBox


class OperationNotice(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("术语操作反馈")
        self.setProperty("tbTerminologySoftCard", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 5, 20, 5)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details = TechnicalDetailsBox(self)
        layout.addWidget(self.message)
        layout.addWidget(self.details)
        self.hide()

    def show_message(self, message, *, details=()):
        self.message.setText(message)
        self.details.set_details(details)
        self.setVisible(bool(message))

    def show_error(self, notice):
        self.show_message(
            f"{notice.title}：{notice.message}\n{notice.impact} {notice.recovery}".strip(),
            details=notice.technical_details,
        )
