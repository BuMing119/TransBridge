from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton
from PyQt6.QtCore import pyqtSignal


class QuickActionsPanel(QWidget):
    """顶部快捷指令面板，提供常用操作的快捷入口。"""

    action_clicked = pyqtSignal(str)

    _ACTIONS = [
        ("翻译选中", "请翻译当前选中的词条"),
        ("质量检查", "请检查当前集合的翻译质量"),
        ("查询术语", "请查询以下术语："),
        ("导出JSON", "请导出当前集合为 JSON"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        for label, prompt in self._ACTIONS:
            btn = QPushButton(label)
            btn.setToolTip(prompt)
            btn.setMinimumHeight(36)
            btn.clicked.connect(lambda checked, p=prompt: self.action_clicked.emit(p))
            layout.addWidget(btn)

        layout.addStretch()
