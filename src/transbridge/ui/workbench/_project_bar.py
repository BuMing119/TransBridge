"""项目工具栏：显示当前项目名、版本选择、项目管理按钮。"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal


class ProjectBar(QWidget):
    """工作台顶部项目工具栏——项目名 + 版本下拉 + 管理按钮。"""

    new_project_requested = pyqtSignal()
    open_project_requested = pyqtSignal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 项目标签
        layout.addWidget(QLabel("项目:"))
        self._project_label = QLabel("无项目")
        self._project_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._project_label)

        # 版本标签
        layout.addWidget(QLabel("版本:"))
        self._variant_label = QLabel("—")
        layout.addWidget(self._variant_label)

        layout.addStretch()

        # 按钮
        self._btn_new = QPushButton("新建项目")
        self._btn_new.setFlat(True)
        self._btn_new.clicked.connect(self.new_project_requested.emit)
        layout.addWidget(self._btn_new)

        self._btn_open = QPushButton("打开项目")
        self._btn_open.setFlat(True)
        self._btn_open.clicked.connect(self.open_project_requested.emit)
        layout.addWidget(self._btn_open)

        # 监听 workspace 变化
        ctx.workspace_changed.connect(self.refresh)

    def refresh(self):
        """根据 ctx 刷新显示。"""
        ws = self._ctx.workspace
        proj = self._ctx.active_project
        variant = self._ctx.active_variant

        if proj:
            self._project_label.setText(proj.name)
            self._variant_label.setText(variant or "—")
            self._btn_new.setVisible(False)
            self._btn_open.setVisible(False)
        else:
            self._project_label.setText("无项目")
            self._variant_label.setText("—")
            self._btn_new.setVisible(True)
            self._btn_open.setVisible(True)
