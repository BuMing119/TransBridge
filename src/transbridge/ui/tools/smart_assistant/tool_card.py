from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PyQt6.QtCore import pyqtSignal


class ToolCard(QWidget):
    """单步工具确认卡片：黄色背景。"""

    executed = pyqtSignal(dict)
    ignored = pyqtSignal(dict)

    def __init__(self, step: dict, parent=None):
        super().__init__(parent)
        self._step = step
        self.setObjectName("ToolCard")
        self.setStyleSheet(
            "#ToolCard { background-color: #FFF8E1; border-radius: 8px; padding: 8px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        tool_name = step.get("tool", "?")
        title = QLabel(f"🔧 {tool_name}")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        # 参数展示
        args = step.get("args", {})
        if args:
            args_text = ", ".join(f"{k}={v}" for k, v in args.items())
            args_label = QLabel(f"参数: {args_text}")
            args_label.setStyleSheet("color: #666; font-size: 11px;")
            args_label.setWordWrap(True)
            layout.addWidget(args_label)

        # 按钮行
        btn_row = QHBoxLayout()
        self._exec_btn = QPushButton("执行")
        self._exec_btn.clicked.connect(self._on_execute)
        self._ignore_btn = QPushButton("忽略")
        self._ignore_btn.clicked.connect(self._on_ignore)
        btn_row.addStretch()
        btn_row.addWidget(self._exec_btn)
        btn_row.addWidget(self._ignore_btn)
        layout.addLayout(btn_row)

        self._result_label = QLabel("")
        self._result_label.setStyleSheet("font-size: 11px;")
        self._result_label.setVisible(False)
        layout.addWidget(self._result_label)

    def set_result(self, success: bool, message: str) -> None:
        icon = "✅" if success else "❌"
        self._result_label.setText(f"{icon} {message}")
        self._result_label.setVisible(True)
        self._exec_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)

    def _on_execute(self):
        self._exec_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.executed.emit(self._step)

    def _on_ignore(self):
        self._exec_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)
        self.ignored.emit(self._step)


class BatchToolCard(QWidget):
    """多步工具确认卡片：黄色背景，步骤概览。"""

    all_executed = pyqtSignal(list)

    def __init__(self, steps: list, parent=None):
        super().__init__(parent)
        self._steps = steps
        self.setObjectName("BatchToolCard")
        self.setStyleSheet(
            "#BatchToolCard { background-color: #FFF8E1; border-radius: 8px; padding: 8px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        title = QLabel(f"🔧 批量执行（共 {len(steps)} 步）")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        for s in steps:
            name = s.get("tool", "?")
            layout.addWidget(QLabel(f"  · {name}"))

        self._exec_btn = QPushButton("全部执行")
        self._exec_btn.clicked.connect(self._on_execute)
        layout.addWidget(self._exec_btn)

    def _on_execute(self):
        self._exec_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.all_executed.emit(self._steps)
