"""动态术语库查看对话框。"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
)


class _TermEditorDialog(QDialog):
    def __init__(self, dynamic_db, parent=None):
        super().__init__(parent)
        self._db = dynamic_db
        self.setWindowTitle("编辑动态术语库")
        self.resize(600, 400)
        self._init_ui()
        self._load_terms()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["原词", "译词", "来源", "上下文"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _load_terms(self):
        entries = self._db.as_list()
        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self._table.setItem(row, 0, QTableWidgetItem(e.term))
            self._table.setItem(row, 1, QTableWidgetItem(e.translation))
            self._table.setItem(row, 2, QTableWidgetItem(e.source))
            self._table.setItem(row, 3, QTableWidgetItem(e.context))