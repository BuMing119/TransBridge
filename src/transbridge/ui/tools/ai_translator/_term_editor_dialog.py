"""动态术语库查看对话框。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .task_widget_style import configure_task_button, configure_task_host, configure_task_title


class _TermEditorDialog(QDialog):
    def __init__(self, dynamic_db, parent=None):
        super().__init__(parent)
        configure_task_host(self)
        self._db = dynamic_db
        self.setWindowTitle("查看动态术语库")
        self.resize(600, 400)
        self._init_ui()
        self._load_terms()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        notice = QLabel("此处只读查看已记录的动态术语，不会修改或保存术语。")
        notice.setWordWrap(True)
        configure_task_title(notice, "hint")
        layout.addWidget(notice)
        self._table = QTableWidget(0, 4)
        ComponentStyle.apply_static(self._table, ComponentKind.TABLE)
        self._table.setAccessibleName("动态术语只读列表")
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setHorizontalHeaderLabels(["原词", "译词", "来源", "上下文"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        configure_task_button(close_btn)
        close_btn.setDefault(True)
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
            for column in range(self._table.columnCount()):
                item = self._table.item(row, column)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(item.text())
