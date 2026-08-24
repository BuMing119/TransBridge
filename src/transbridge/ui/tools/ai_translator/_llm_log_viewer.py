"""LLM 原始响应日志查看窗口（多并发批次，每批次独立 Tab）。"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ElidedLabel


class _LLMLogViewer(QWidget):
    """独立窗口，实时显示各并发批次的 LLM 流式响应日志，每批次一个 Tab。"""

    def __init__(self, log_dir: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._log_dir = log_dir
        self._known_files: set[str] = set()
        self._tab_paths: dict[int, str] = {}  # tab_index → file_path

        self.setWindowTitle(f"LLM 原始日志 — {os.path.basename(log_dir)}")
        self.resize(900, 640)
        self._init_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start(800)
        self._scan_and_refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # 工具栏
        toolbar = QHBoxLayout()
        path_lbl = ElidedLabel(self._log_dir)
        path_lbl.setAccessibleName("LLM 日志目录")
        path_lbl.setAccessibleDescription(self._log_dir)
        path_lbl.setToolTip(self._log_dir)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_label = path_lbl
        toolbar.addWidget(path_lbl, 1)

        self._auto_cb = QCheckBox("自动刷新")
        self._auto_cb.setChecked(True)
        toolbar.addWidget(self._auto_cb)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(56)
        refresh_btn.clicked.connect(self._scan_and_refresh)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        # Tab 区域（每批次一个 Tab）
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

    def _on_timer(self):
        if self._auto_cb.isChecked():
            self._scan_and_refresh()

    def _scan_and_refresh(self):
        if not os.path.isdir(self._log_dir):
            return

        try:
            files = sorted(f for f in os.listdir(self._log_dir) if f.endswith(".log"))
        except Exception:
            return

        # 为新出现的日志文件创建 Tab
        for fname in files:
            if fname not in self._known_files:
                self._known_files.add(fname)
                path = os.path.join(self._log_dir, fname)
                text_edit = QTextEdit()
                text_edit.setReadOnly(True)
                text_edit.setFont(QFont("Consolas", 9))
                # "batch_003.log" → "批次 3"
                label = fname.replace("batch_", "").replace(".log", "").lstrip("0") or "1"
                tab_idx = self._tabs.addTab(text_edit, f"批次 {label}")
                self._tab_paths[tab_idx] = path
                self._tabs.setCurrentIndex(tab_idx)

        # 刷新所有 Tab 内容
        for tab_idx in range(self._tabs.count()):
            path = self._tab_paths.get(tab_idx)
            if not path:
                continue
            widget = self._tabs.widget(tab_idx)
            if isinstance(widget, QTextEdit):
                self._refresh_tab(widget, path)

    def _refresh_tab(self, text_edit: QTextEdit, path: str):
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return

        if content == text_edit.toPlainText():
            return

        sb = text_edit.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        text_edit.setPlainText(content)
        if at_bottom:
            sb.setValue(sb.maximum())

    def stop_auto_refresh(self):
        """翻译结束后调用，停止定时器并做最后一次刷新。"""
        self._timer.stop()
        self._auto_cb.setChecked(False)
        self._scan_and_refresh()

    def closeEvent(self, event):
        self._timer.stop()
        event.accept()
