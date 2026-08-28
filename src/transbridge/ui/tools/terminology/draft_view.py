"""Manual-adjustment page for terminology drafts."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from .paged_models import KeysetPagedTableModel


class DraftView(QWidget):
    add_requested = pyqtSignal()
    edit_requested = pyqtSignal(object)
    suppress_requested = pyqtSignal(object)
    publish_requested = pyqtSignal()

    def __init__(self, model: KeysetPagedTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        controls = QHBoxLayout()
        self.add_button = QPushButton("＋  新增术语", self)
        self.add_button.setProperty("tbTerminologyPrimary", True)
        self.edit_button = QPushButton("调整所选", self)
        self.suppress_button = QPushButton("不再使用", self)
        self.publish_button = QPushButton("发布新版…", self)
        self.add_button.clicked.connect(self.add_requested)
        self.edit_button.clicked.connect(lambda: self._emit_selected(self.edit_requested))
        self.suppress_button.clicked.connect(lambda: self._emit_selected(self.suppress_requested))
        self.publish_button.clicked.connect(self.publish_requested)
        for button in (self.add_button, self.edit_button, self.suppress_button, self.publish_button):
            if button is not self.add_button:
                button.setProperty("tbComponentKind", "button")
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.empty_status = QLabel("尚无术语。请先在“概览”创建术语库。", self)
        self.empty_status.setProperty("tbSecondary", True)
        layout.addWidget(self.empty_status)
        model.rowsInserted.connect(self._sync_empty_state)
        model.modelReset.connect(self._sync_empty_state)

    def _emit_selected(self, signal) -> None:
        index = self.table.currentIndex()
        if index.isValid():
            signal.emit(index.data(Qt.ItemDataRole.UserRole))

    def _sync_empty_state(self, *_args) -> None:
        self.empty_status.setVisible(self.model.rowCount() == 0)


__all__ = ["DraftView"]
