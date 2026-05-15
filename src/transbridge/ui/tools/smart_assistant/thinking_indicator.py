"""思考过程折叠指示器（FR7.16 / Story-08-5）。

默认显示"正在思考中..."加三点循环动画，非气泡紧凑横条样式。
按 Ctrl+O 展开详细 thought 文本，再次按 Ctrl+O 或点击按钮折叠。
"""

from __future__ import annotations

from PyQt6 import sip

from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QTextEdit, QPushButton
from PyQt6.QtCore import Qt, QTimer


class ThinkingIndicator(QWidget):
    """思考动画指示器 + 可折叠详细内容。"""

    _ANIM_DOTS = [".  ", ".. ", "..."]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "ThinkingIndicator {"
            "  background-color: #f5f5f5; border-radius: 8px;"
            "  margin: 2px 0;"
            "}"
        )

        self._expanded = False
        self._anim_idx = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)

        # 默认行：图标 + "正在思考中..." + 动画点 + 展开按钮
        default_row = QHBoxLayout()
        default_row.setSpacing(6)

        self._icon = QLabel("")
        self._icon.setStyleSheet(
            "QLabel { color: #999; font-size: 13px; background: transparent; border: none; }"
        )
        default_row.addWidget(self._icon)

        self._label = QLabel("正在思考中")
        self._label.setStyleSheet(
            "QLabel { color: #888; font-size: 12px; background: transparent; border: none; }"
        )
        default_row.addWidget(self._label)

        self._dots = QLabel(self._ANIM_DOTS[0])
        self._dots.setStyleSheet(
            "QLabel { color: #999; font-size: 12px; background: transparent; border: none;"
            "  min-width: 28px; }"
        )
        default_row.addWidget(self._dots)

        default_row.addStretch()

        self._toggle_btn = QPushButton("展开")
        self._toggle_btn.setStyleSheet(
            "QPushButton {"
            "  background: transparent; border: 1px solid #ddd; border-radius: 4px;"
            "  padding: 1px 8px; font-size: 10px; color: #888;"
            "}"
            "QPushButton:hover { background: #e8e8e8; color: #555; }"
        )
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self.toggle_expand)
        default_row.addWidget(self._toggle_btn)

        layout.addLayout(default_row)

        # 展开内容区（默认隐藏）
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setMaximumHeight(200)
        self._detail.setStyleSheet(
            "QTextEdit {"
            "  font-family: 'Consolas', 'Courier New', monospace;"
            "  font-size: 11px; background: #fafafa; border: 1px solid #e0e0e0;"
            "  border-radius: 4px; padding: 6px; color: #555;"
            "}"
        )
        self._detail.setVisible(False)
        layout.addWidget(self._detail)

        self._thought_text = ""

        # 动画 timer
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._anim_tick)

    def set_thought(self, text: str) -> None:
        """设置思考文本并启动动画。"""
        self._thought_text = text
        self._detail.setPlainText(text)
        self._anim_idx = 0
        self._dots.setText(self._ANIM_DOTS[0])
        self._timer.start()
        self.show()

    def toggle_expand(self) -> None:
        """展开/折叠详细内容。"""
        self._expanded = not self._expanded
        if self._expanded:
            self._timer.stop()
            self._detail.setVisible(True)
            self._toggle_btn.setText("收起")
            self._label.setText("思考过程")
            self._dots.setText("")
        else:
            self._detail.setVisible(False)
            self._toggle_btn.setText("展开")
            self._label.setText("正在思考中")
            self._anim_idx = 0
            self._dots.setText(self._ANIM_DOTS[0])
            self._timer.start()

    def stop_animation(self) -> None:
        """停止动画（工具执行完成/下一轮开始时调用）。"""
        self._timer.stop()

    def clear(self) -> None:
        """停止动画并隐藏。"""
        self._timer.stop()
        self.hide()

    def _anim_tick(self) -> None:
        """三点循环动画。

        QA-004: sip.isdeleted 守卫，防止 Widget 已销毁时定时器回调崩溃。
        """
        if sip.isdeleted(self._dots):
            self._timer.stop()
            return
        self._anim_idx = (self._anim_idx + 1) % len(self._ANIM_DOTS)
        self._dots.setText(self._ANIM_DOTS[self._anim_idx])
