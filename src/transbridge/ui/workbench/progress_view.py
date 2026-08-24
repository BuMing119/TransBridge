"""Operation progress view shared by Workbench operations."""

from PyQt6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class ProgressView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        self.label = QLabel("")
        self.label.setStyleSheet("color: #555; font-size: 12px;")
        layout.addWidget(self.bar)
        layout.addWidget(self.label)
        self.hide()

    def show_progress(self, total: int, message: str = "") -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(0)
        else:
            self.bar.setRange(0, 0)
        self.label.setText(message)
        self.show()

    def update_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(current)
        self.label.setText(message)

    def hide_progress(self) -> None:
        self.hide()
        self.bar.setValue(0)
        self.label.clear()
