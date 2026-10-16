"""Bounded-widget, selectable full-text review of translation before/after rows."""

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QPlainTextEdit, QTableView, QVBoxLayout, QWidget


class ChangeTableModel(QAbstractTableModel):
    headers = ("词条", "原文", "修改前译文", "修改后译文", "说明")

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = tuple(rows)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def values(self, row):
        item = self.rows[row]
        return (
            item.entry_id,
            "未保存或无法核验原文" if item.original is None else item.original,
            "未知，无法核验旧值" if item.before is None else item.before,
            "执行时生成，尚未确定" if item.after is None else item.after,
            item.detail,
        )

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self.values(index.row())[index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return super().headerData(section, orientation, role)


class ChangeReviewView(QWidget):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.model = ChangeTableModel(rows, self)
        self.table = QTableView()
        self.table.setAccessibleName("词条变更对照")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)
        self.full_text = QPlainTextEdit()
        self.full_text.setReadOnly(True)
        self.full_text.setAccessibleName("所选词条完整变更，可选择复制")
        self.full_text.setMaximumHeight(170)
        self.table.selectionModel().currentRowChanged.connect(self._show_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.full_text)
        if self.model.rows:
            self.table.selectRow(0)

    def _show_row(self, index, _previous):
        if index.isValid():
            values = zip(self.model.headers, self.model.values(index.row()), strict=True)
            self.full_text.setPlainText("\n\n".join(f"{name}：\n{value}" for name, value in values))

    def selected_ids(self):
        return tuple(
            dict.fromkeys(self.model.rows[index.row()].entry_id for index in self.table.selectionModel().selectedRows())
        )
