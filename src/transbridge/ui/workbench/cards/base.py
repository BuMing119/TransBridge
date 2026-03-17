from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QLabel, QPushButton


class OpCard(QGroupBox):
    """单个操作卡片：标题 + 说明 + 操作按钮。"""

    def __init__(self, title: str, desc: str, btn_text: str, parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        lbl = QLabel(desc)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #555;")
        layout.addWidget(lbl)
        layout.addStretch()
        self.btn = QPushButton(btn_text)
        self.btn.setFixedHeight(30)
        layout.addWidget(self.btn)
