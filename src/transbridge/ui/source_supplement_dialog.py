"""Choose exact entry correspondences without creating an editor for every row."""

from PyQt6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QLabel, QTableView, QVBoxLayout

from transbridge.ui.foundation.components import configure_dialog


class _Choices(QAbstractListModel):
    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.choices = choices
        self.indices = {choice.key: index + 1 for index, choice in enumerate(choices)}
        self.by_key = {choice.key: choice for choice in choices}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.choices) + 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if index.row() == 0:
            return "暂不对应，保留这条译文" if role == Qt.ItemDataRole.DisplayRole else None
        choice = self.choices[index.row() - 1]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{choice.label} — {choice.original[:120]}"
        if role == Qt.ItemDataRole.UserRole:
            return choice.key
        if role == Qt.ItemDataRole.ToolTipRole:
            return choice.original
        return None


class _Mappings(QAbstractTableModel):
    def __init__(self, rows, choices, parent=None):
        super().__init__(parent)
        self.rows, self.choices = rows, choices
        self.mappings = {row.old_key: row.matched_key for row in rows}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("已保存条目键", "已保存译文", "对应输入条目", "输入中的原文")[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        row = self.rows[index.row()]
        choice = self.choices.by_key.get(self.mappings[row.old_key])
        return (
            row.old_key.local_key,
            row.translation,
            choice.label if choice else "暂不对应，保留译文",
            choice.original if choice else "",
        )[index.column()]


class SourceSupplementDialog(QDialog):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("补充处理所需的输入")
        self.resize(900, 540)
        self._selected = -1
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"输入：{preview.source_name}\n{preview.replacement_path}\n"
            f"将应用到工程的 {preview.variant_count} 个翻译版本；自动对应 {preview.matched_count} 条、"
            f"待手动对应 {preview.unmatched_count} 条、新输入新增 {preview.added_count} 条。\n"
            "暂不对应的译文仍保留在工程中，不会删除，也不纳入此次依赖输入的操作。",
            self,
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.choices = _Choices(preview.choices, self)
        self.mapping_model = _Mappings(preview.rows, self.choices, self)
        self.table = QTableView(self)
        self.table.setModel(self.mapping_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAccessibleName("保存译文与实际输入的对应关系")
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.selector = QComboBox(self)
        self.selector.setModel(self.choices)
        self.selector.setAccessibleName("为选中的保存译文选择输入词条")
        self.selector.setEnabled(bool(preview.rows))
        self.selector.currentIndexChanged.connect(self._mapped)
        self.table.selectionModel().currentRowChanged.connect(self._selected_row)
        layout.addWidget(self.selector)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存输入并继续")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消当前操作")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if preview.rows:
            self.table.setCurrentIndex(self.mapping_model.index(0, 0))

    def _selected_row(self, current, _previous):
        if not current.isValid():
            self._selected = -1
            return
        self._selected = current.row()
        key = self.mapping_model.mappings[self.mapping_model.rows[self._selected].old_key]
        self.selector.blockSignals(True)
        self.selector.setCurrentIndex(self.choices.indices.get(key, 0))
        self.selector.blockSignals(False)

    def _mapped(self, _index):
        if self._selected < 0:
            return
        self.mapping_model.mappings[self.mapping_model.rows[self._selected].old_key] = self.selector.currentData()
        self.mapping_model.dataChanged.emit(
            self.mapping_model.index(self._selected, 2), self.mapping_model.index(self._selected, 3)
        )

    def mappings(self):
        return dict(self.mapping_model.mappings)
