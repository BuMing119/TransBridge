"""
ProjectPromptOverlay: 当集合已加载但未选中 ParaTranz 项目时，
覆盖在 Step3 操作区上方的引导层。
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import pyqtSignal, Qt


class ProjectPromptOverlay(QWidget):
    """覆盖在 Step3 操作区上方的项目选择引导层。"""

    go_to_pt = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "ProjectPromptOverlay { background: rgba(235, 245, 251, 230); }"
        )

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        hint = QLabel("集合已加载。若要上传或下载合并，\n请先在「ParaTranz 管理」中选择一个项目。")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #1a5276; font-size: 13px;")
        layout.addWidget(hint)

        btn = QPushButton("前往 ParaTranz 管理  →  选择项目")
        btn.setFixedHeight(44)
        btn.setFixedWidth(320)
        btn.setStyleSheet(
            "QPushButton {"
            "  background: #2980b9; color: white; border: none;"
            "  border-radius: 4px; font-size: 14px;"
            "}"
            "QPushButton:hover { background: #2471a3; }"
            "QPushButton:pressed { background: #1a5276; }"
        )
        btn.clicked.connect(self.go_to_pt)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.hide()
