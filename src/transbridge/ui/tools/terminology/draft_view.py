"""Manual-adjustment page for terminology drafts."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QPushButton, QTableView, QVBoxLayout, QWidget

from .paged_models import KeysetPagedTableModel


class DraftView(QWidget):
    add_requested = pyqtSignal()
    edit_requested = pyqtSignal(object)
    suppress_requested = pyqtSignal(object)

    def __init__(self, model: KeysetPagedTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.add_button = QPushButton("＋  新增术语", self)
        self.add_button.setProperty("tbComponentKind", "button")
        self.row_actions = QWidget(self)
        controls = QHBoxLayout(self.row_actions)
        controls.setContentsMargins(0, 0, 0, 0)
        self.edit_button = QPushButton("编辑", self.row_actions)
        self.suppress_button = QPushButton("停用", self.row_actions)
        self.add_button.clicked.connect(self.add_requested)
        self.edit_button.clicked.connect(lambda: self._emit_selected(self.edit_requested))
        self.suppress_button.clicked.connect(lambda: self._emit_selected(self.suppress_requested))
        for button in (self.edit_button, self.suppress_button):
            button.setProperty("tbComponentKind", "button")
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addWidget(self.row_actions)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        self.model.modelReset.connect(self._update_actions)
        self.model.dataChanged.connect(self._update_actions)
        self.table.doubleClicked.connect(lambda _index: self._emit_selected(self.edit_requested))
        layout.addWidget(self.table, 1)
        self._update_actions()

    def _update_actions(self, *_args) -> None:
        rows = self.table.selectionModel().selectedRows()
        self.row_actions.setVisible(len(rows) == 1)
        item = rows[0].data(Qt.ItemDataRole.UserRole) if len(rows) == 1 else None
        self.suppress_button.setText("启用" if getattr(item, "suppressed", False) else "停用")

    def _emit_selected(self, signal) -> None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) == 1:
            signal.emit(rows[0].data(Qt.ItemDataRole.UserRole))


__all__ = ["DraftView"]
