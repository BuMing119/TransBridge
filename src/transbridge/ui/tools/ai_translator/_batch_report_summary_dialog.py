"""批量翻译报告汇总对话框。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal

if TYPE_CHECKING:
    pass

STATUS_ICONS = {"success": "✅", "stopped": "⚠️", "failed": "❌"}


class _BatchReportSummaryDialog(QDialog):
    """批量翻译/润色完成后弹出的跨插件汇总弹窗。

    每个插件一行，显示状态和关键统计。双击行打开该插件的详细报告。
    """

    open_plugin_report = pyqtSignal(int)  # plugin_index

    def __init__(self, plugin_results: list[dict], parent=None):
        super().__init__(parent)
        self._plugin_results = plugin_results
        self.setWindowTitle("批量翻译报告汇总")
        self.resize(750, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"共 {len(plugin_results)} 个插件，"
                                f"成功 {sum(1 for p in plugin_results if p['status'] == 'success')}，"
                                f"失败 {sum(1 for p in plugin_results if p['status'] == 'failed')}"))

        # 表格
        col_headers = ["插件名", "状态", "成功", "失败", "跳过", "需审核"]
        self._table = QTableWidget()
        self._table.setColumnCount(len(col_headers))
        self._table.setHorizontalHeaderLabels(col_headers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.cellDoubleClicked.connect(self._on_double_click)

        self._table.setRowCount(len(plugin_results))
        for i, p in enumerate(plugin_results):
            self._table.setItem(i, 0, QTableWidgetItem(p.get("esp_stem", "?")))
            icon = STATUS_ICONS.get(p.get("status", "failed"), "?")
            self._table.setItem(i, 1, QTableWidgetItem(f"{icon} {p.get('status', '?')}"))
            self._table.setItem(i, 2, QTableWidgetItem(str(p.get("success", 0))))
            self._table.setItem(i, 3, QTableWidgetItem(str(p.get("failed", 0))))
            self._table.setItem(i, 4, QTableWidgetItem(str(p.get("skipped", 0))))
            self._table.setItem(i, 5, QTableWidgetItem(str(p.get("needs_review", 0))))

        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        # 底部按钮
        bar = QHBoxLayout()
        self._btn_dir = QPushButton("打开报告目录")
        self._btn_dir.clicked.connect(self._on_open_dir)
        bar.addWidget(self._btn_dir)
        bar.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bar.addWidget(btn_close)
        layout.addLayout(bar)

    def _on_double_click(self, row: int, col: int):
        """双击行 → 发射信号打开该插件的详细报告。"""
        p = self._plugin_results[row]
        if p["status"] in ("success", "stopped"):
            self.open_plugin_report.emit(row)
            self.accept()

    def _on_open_dir(self):
        """打开第一个有报告路径的插件所在目录，或 data/ai_translator/ 目录。"""
        for p in self._plugin_results:
            rp = p.get("report_path")
            if rp and os.path.exists(rp):
                os.startfile(os.path.dirname(rp))
                return
        # fallback: 尝试打开 data/ai_translator/
        data_dir = os.path.join("data", "ai_translator")
        if os.path.isdir(data_dir):
            os.startfile(data_dir)
