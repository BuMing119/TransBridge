"""LLM 原始响应日志查看窗口。"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


class _LLMLogViewer(QWidget):
    """独立窗口，实时显示写入文件的 LLM 流式响应日志。"""

    def __init__(self, log_path: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._log_path = log_path
        fname = os.path.basename(log_path)
        self.setWindowTitle(f"LLM 原始日志 — {fname}")
        self.resize(860, 640)
        self._init_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start(800)
        self._refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # 工具栏
        toolbar = QHBoxLayout()
        path_lbl = QLabel(self._log_path)
        path_lbl.setStyleSheet("font-size: 10px; color: #888;")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar.addWidget(path_lbl, 1)

        self._auto_cb = QCheckBox("自动刷新")
        self._auto_cb.setChecked(True)
        toolbar.addWidget(self._auto_cb)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(56)
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        # 文本区
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 9))
        layout.addWidget(self._text)

    def _on_timer(self):
        if self._auto_cb.isChecked():
            self._refresh()

    def _refresh(self):
        if not os.path.exists(self._log_path):
            return
        try:
            with open(self._log_path, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return

        if content == self._text.toPlainText():
            return

        sb = self._text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self._text.setPlainText(content)
        if at_bottom:
            sb.setValue(sb.maximum())

    def stop_auto_refresh(self):
        """翻译结束后调用，停止定时器并做最后一次刷新。"""
        self._timer.stop()
        self._auto_cb.setChecked(False)
        self._refresh()

    def closeEvent(self, event):
        self._timer.stop()
        event.accept()