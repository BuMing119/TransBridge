"""Immutable terminology-version history page."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from .paged_models import KeysetPagedTableModel


class HistoryView(QWidget):
    compare_requested = pyqtSignal(object)
    restore_requested = pyqtSignal(object)

    def __init__(self, model: KeysetPagedTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        controls = QHBoxLayout()
        compare = QPushButton("与当前版本比较", self)
        restore = QPushButton("以此内容创建新版…", self)
        compare.setProperty("tbComponentKind", "button")
        restore.setProperty("tbComponentKind", "button")
        compare.clicked.connect(lambda: self._emit_selected(self.compare_requested))
        restore.clicked.connect(lambda: self._emit_selected(self.restore_requested))
        controls.addWidget(compare)
        controls.addWidget(restore)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.status = QLabel("尚无已发布版本。", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        model.rowsInserted.connect(self._sync_status)
        model.modelReset.connect(self._sync_status)

    def set_status(self, message: str) -> None:
        self.status.setText(message)

    def _sync_status(self, *_args) -> None:
        self.status.setText("只加载当前可见页" if self.model.rowCount() else "尚无已发布版本。")

    def _emit_selected(self, signal) -> None:
        index = self.table.currentIndex()
        if index.isValid():
            signal.emit(index.data(Qt.ItemDataRole.UserRole))


__all__ = ["HistoryView"]
