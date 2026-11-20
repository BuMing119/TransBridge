# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""A record header and a virtual table of its translatable fields."""

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .models import RecordFieldsModel


class RecordFieldsView(QWidget):
    entry_selected = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        self.heading = QLabel(self)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.heading.setAccessibleName("当前 ESP 记录")
        layout.addWidget(self.heading)
        self.table = QTableView(self)
        self.table.setAccessibleName("ESP 记录的可翻译字段")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.model = RecordFieldsModel(self.table)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.selectionModel().currentRowChanged.connect(self._selected)
        layout.addWidget(self.table, 1)
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)

    def _selected(self, current, _old) -> None:
        if current.isValid():
            self.entry_selected.emit(current.row())

    def show_record(self, entries, row: int, *, record_type="", editor_id="", form_id="") -> None:
        self.heading.setText(f"类型：{record_type or '—'}    EditorID：{editor_id or '—'}    FormID：{form_id or '—'}")
        with QSignalBlocker(self.table.selectionModel()):
            self.model.set_entries(entries)
            self.table.selectRow(row)
            self.table.scrollTo(self.model.index(row, 0))
