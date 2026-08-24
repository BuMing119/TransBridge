"""Operation progress view shared by Workbench operations."""

from PyQt6.QtWidgets import QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ElidedLabel


class ProgressView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self.setSizePolicy(policy)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        self.bar.setAccessibleName("操作进度")
        self.label = ElidedLabel("")
        label_font = self.label.font()
        label_font.setPointSize(9)
        self.label.setFont(label_font)
        self.label.setAccessibleName("操作进度状态")
        layout.addWidget(self.bar)
        layout.addWidget(self.label)
        self.hide()

    def show_progress(self, total: int, message: str = "") -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(0)
        else:
            self.bar.setRange(0, 0)
        self.label.set_full_text(message)
        self.label.setToolTip(message)
        self.label.setAccessibleDescription(message)
        self.show()

    def update_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(current)
        self.label.set_full_text(message)
        self.label.setToolTip(message)
        self.label.setAccessibleDescription(message)

    def hide_progress(self) -> None:
        self.hide()
        self.bar.setValue(0)
        self.label.set_full_text("")
        self.label.setToolTip("")
