# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Persistent secondary windows for advanced terminology tasks."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologyTaskDialog(QDialog):
    def __init__(self, title, parent, *, description=""):
        super().__init__(parent, Qt.WindowType.Window)
        self.close_guard = None
        parent.installEventFilter(self)
        self.setWindowTitle(title)
        self.setProperty("tbTaskDialog", True)
        self.resize(1100, 720)
        self.setMinimumSize(320, 240)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(scroll)
        self.content = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        if description:
            label = QLabel(description, self)
            label.setWordWrap(True)
            self.content.addWidget(label)
        self.close_button = QPushButton("完成并返回", self)
        ComponentStyle.apply_static(self.close_button, ComponentKind.BUTTON)
        self.close_button.clicked.connect(self.close)

    def open_task(self):
        # Search fields own Enter; page buttons must not become dialog defaults.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.show()
        available = self.screen().availableGeometry()
        margin = self.frameGeometry().height() - self.height()
        self.resize(min(self.width(), available.width() - 24), min(self.height(), available.height() - margin - 24))
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())
        self.raise_()
        self.activateWindow()

    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            self.hide()
        return super().eventFilter(watched, event)

    def reject(self):
        if self.close_guard is None or self.close_guard():
            super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self.close_guard is not None and not self.close_guard():
            event.ignore()
            return
        event.accept()
