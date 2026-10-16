"""Read-only view of the exact effective snapshot used by translation."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

from .pagination_bar import PaginationBar
from .term_filters import TermFilterBar


class EffectiveTermsView(QWidget):
    query_changed = pyqtSignal(object)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.description = QLabel("后续翻译使用的已发布项目术语与当前译名方案；草稿中的调整需发布后生效。", self)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.filters = TermFilterBar(self)
        self.filters.changed.connect(self.query_changed)
        layout.addWidget(self.filters)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.pagination = PaginationBar(model, self)
        layout.addWidget(self.pagination)
