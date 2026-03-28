"""
批量翻译 LLM 日志查看窗口。

支持：
- 插件级别的 Tab
- 每个插件内显示所有批次日志
- 实时刷新
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QCheckBox, QTabWidget, QSplitter,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

if TYPE_CHECKING:
    pass


class _PluginLogWidget(QWidget):
    """单个插件的日志显示组件。"""

    def __init__(self, plugin_dir: str, parent=None):
        super().__init__(parent)
        self._plugin_dir = plugin_dir
        self._known_files: set[str] = set()
        self._batch_tabs: dict[str, int] = {}  # filename -> tab_index

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 批次 Tab 区域
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.South)
        layout.addWidget(self._tabs)

    def refresh(self):
        """刷新日志内容。"""
        if not os.path.isdir(self._plugin_dir):
            return

        try:
            # 获取所有批次日志文件，按名称排序
            files = sorted(
                f for f in os.listdir(self._plugin_dir)
                if f.endswith(".log") and f.startswith("batch_")
            )
        except Exception:
            return

        # 为新出现的日志文件创建 Tab
        for fname in files:
            if fname not in self._known_files:
                self._known_files.add(fname)
                path = os.path.join(self._plugin_dir, fname)

                text_edit = QTextEdit()
                text_edit.setReadOnly(True)
                text_edit.setFont(QFont("Consolas", 9))
                text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

                # "batch_003.log" → "3"
                label = fname.replace("batch_", "").replace(".log", "").lstrip("0") or "1"
                tab_idx = self._tabs.addTab(text_edit, f"批次 {label}")
                self._batch_tabs[fname] = tab_idx

        # 刷新所有 Tab 内容
        for fname, tab_idx in self._batch_tabs.items():
            path = os.path.join(self._plugin_dir, fname)
            widget = self._tabs.widget(tab_idx)
            if isinstance(widget, QTextEdit):
                self._refresh_text(widget, path)

    def _refresh_text(self, text_edit: QTextEdit, path: str):
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
        """停止自动刷新（目前无定时器，保留接口）。"""
        pass


class _BatchLLMLogViewer(QWidget):
    """批量翻译日志查看窗口：插件为一级 Tab，批次为二级 Tab。"""

    def __init__(self, log_dir: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._log_dir = log_dir
        self._known_plugins: set[str] = set()
        self._plugin_widgets: dict[str, _PluginLogWidget] = {}

        self.setWindowTitle(f"批量翻译 LLM 日志 — {os.path.basename(log_dir)}")
        self.resize(1000, 700)
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
        path_lbl = QLabel(self._log_dir)
        path_lbl.setStyleSheet("font-size: 10px; color: #888;")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar.addWidget(path_lbl, 1)

        self._auto_cb = QCheckBox("自动刷新")
        self._auto_cb.setChecked(True)
        toolbar.addWidget(self._auto_cb)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(56)
        refresh_btn.clicked.connect(self._scan_and_refresh)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        # 插件 Tab 区域
        self._plugin_tabs = QTabWidget()
        layout.addWidget(self._plugin_tabs)

    def _on_timer(self):
        if self._auto_cb.isChecked():
            self._scan_and_refresh()

    def _scan_and_refresh(self):
        if not os.path.isdir(self._log_dir):
            return

        try:
            # 获取所有子目录（插件目录）
            entries = os.listdir(self._log_dir)
            plugin_dirs = [
                e for e in entries
                if os.path.isdir(os.path.join(self._log_dir, e))
            ]
        except Exception:
            return

        # 为新出现的插件目录创建 Tab
        for plugin_name in sorted(plugin_dirs):
            if plugin_name not in self._known_plugins:
                self._known_plugins.add(plugin_name)
                plugin_dir = os.path.join(self._log_dir, plugin_name)

                widget = _PluginLogWidget(plugin_dir)
                tab_idx = self._plugin_tabs.addTab(widget, plugin_name)
                self._plugin_widgets[plugin_name] = widget

        # 刷新所有插件组件
        for widget in self._plugin_widgets.values():
            widget.refresh()

    def stop_auto_refresh(self):
        """翻译结束后调用，停止定时器并做最后一次刷新。"""
        self._timer.stop()
        self._auto_cb.setChecked(False)
        self._scan_and_refresh()
        for widget in self._plugin_widgets.values():
            widget.stop_auto_refresh()

    def closeEvent(self, event):
        self._timer.stop()
        event.accept()
