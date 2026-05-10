"""历史报告查看对话框。

扫描 data/ai_translator/*/reports/ 目录下的所有 .xlsx 报告文件，支持查看、打开、删除。
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QAbstractItemView, QMenu, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction


def _parse_report_filename(filename: str) -> dict:
    """解析报告文件名，提取 esp_stem、mode、timestamp。

    文件名格式: {esp_stem}_{mode}_report_{YYYYMMDD_HHMMSS}.xlsx
    """
    result = {"esp_stem": "?", "mode": "?", "timestamp": None}
    name = filename.replace(".xlsx", "")
    # 匹配模式: <esp>_<mode>_report_<YYYYMMDD_HHMMSS>
    m = re.match(r"(.+)_(translate|polish)_report_(\d{8}_\d{6})$", name)
    if m:
        result["esp_stem"] = m.group(1)
        mode_map = {"translate": "翻译", "polish": "润色"}
        result["mode"] = mode_map.get(m.group(2), m.group(2))
        try:
            result["timestamp"] = datetime.strptime(m.group(3), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return result


def _scan_reports() -> list[dict]:
    """扫描 data/ai_translator/*/reports/ 下所有 .xlsx 文件。"""
    reports = []
    base_dir = os.path.join("data", "ai_translator")
    if not os.path.isdir(base_dir):
        return reports

    for esp_dir in os.listdir(base_dir):
        reports_dir = os.path.join(base_dir, esp_dir, "reports")
        if not os.path.isdir(reports_dir):
            continue
        for fname in os.listdir(reports_dir):
            if not fname.endswith(".xlsx"):
                continue
            full_path = os.path.join(reports_dir, fname)
            info = _parse_report_filename(fname)
            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = 0
            reports.append({
                "path": full_path,
                "filename": fname,
                "esp_stem": info["esp_stem"],
                "mode": info["mode"],
                "timestamp": info["timestamp"],
                "size": size,
            })

    # 按时间降序
    reports.sort(key=lambda r: r["timestamp"] or datetime.min, reverse=True)
    return reports


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    elif size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


class _ReportHistoryDialog(QDialog):
    """历史报告查看对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史报告")
        self.resize(750, 480)
        self._reports: list[dict] = []

        layout = QVBoxLayout(self)

        # 表格
        headers = ["文件名", "类型", "插件名", "生成时间", "文件大小"]
        self._table = QTableWidget()
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table)

        # 空状态提示
        self._empty_label = QLabel("暂无历史报告")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #999; font-size: 14px; padding: 60px;")
        self._empty_label.hide()
        layout.addWidget(self._empty_label)

        # 底部按钮
        bar = QHBoxLayout()
        self._btn_delete = QPushButton("删除选中")
        self._btn_delete.clicked.connect(self._on_delete_selected)
        self._btn_delete.setEnabled(False)
        bar.addWidget(self._btn_delete)
        bar.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bar.addWidget(btn_close)
        layout.addLayout(bar)

        self._populate()

        # 选择变化时更新删除按钮
        self._table.itemSelectionChanged.connect(
            lambda: self._btn_delete.setEnabled(len(self._table.selectedItems()) > 0)
        )

    def _populate(self):
        self._reports = _scan_reports()
        if not self._reports:
            self._table.hide()
            self._empty_label.show()
            return

        self._empty_label.hide()
        self._table.show()
        self._table.setRowCount(len(self._reports))
        for i, r in enumerate(self._reports):
            self._table.setItem(i, 0, QTableWidgetItem(r["filename"]))
            self._table.setItem(i, 1, QTableWidgetItem(r["mode"]))
            self._table.setItem(i, 2, QTableWidgetItem(r["esp_stem"]))
            ts_str = r["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if r["timestamp"] else "?"
            self._table.setItem(i, 3, QTableWidgetItem(ts_str))
            self._table.setItem(i, 4, QTableWidgetItem(_format_size(r["size"])))

        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def _on_double_click(self, row: int, col: int):
        if 0 <= row < len(self._reports):
            path = self._reports[row]["path"]
            if os.path.exists(path):
                os.startfile(path)

    def _on_context_menu(self, pos):
        row = self._table.currentRow()
        if row < 0:
            return
        menu = QMenu(self)
        open_action = menu.addAction("打开")
        open_dir_action = menu.addAction("打开所在目录")
        menu.addSeparator()
        delete_action = menu.addAction("删除")

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == open_action:
            self._on_double_click(row, 0)
        elif action == open_dir_action:
            path = self._reports[row]["path"]
            os.startfile(os.path.dirname(path))
        elif action == delete_action:
            self._delete_row(row)

    def _on_delete_selected(self):
        """删除所有选中的行。"""
        rows = sorted(set(i.row() for i in self._table.selectedItems()), reverse=True)
        if not rows:
            return
        reply = QMessageBox.question(
            self, "删除报告", f"确定要删除 {len(rows)} 份报告吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for row in rows:
            self._delete_row(row, confirm=False)

    def _delete_row(self, row: int, confirm: bool = True):
        path = self._reports[row]["path"]
        if confirm:
            reply = QMessageBox.question(
                self, "删除报告",
                f"确定要删除报告「{self._reports[row]['filename']}」吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            os.remove(path)
        except OSError:
            QMessageBox.warning(self, "删除失败", f"无法删除文件:\n{path}")
        self._populate()
