"""Read-only browsing of the exact terminology profile selected for this project."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class SourceTermsModel(QAbstractTableModel):
    """Expose published mappings, never the mutable profile draft."""

    HEADERS = ("原名", "采用译名", "使用范围")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mappings = ()

    def set_mappings(self, mappings) -> None:
        self.beginResetModel()
        self.mappings = tuple(mappings)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.mappings)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802 - Qt API
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.mappings):
            return None
        item = self.mappings[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return (item.original, item.translation, item.plugin_id or "整个项目")[index.column()]
        return None


class _SourceFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.query = ""
        self.scope = ""

    def filterAcceptsRow(self, row, parent):  # noqa: N802 - Qt API
        model = self.sourceModel()
        item = model.data(model.index(row, 0, parent), Qt.ItemDataRole.UserRole)
        return (not self.scope or item.scope_kind == self.scope) and (
            not self.query or self.query in item.original.casefold() or self.query in item.translation.casefold()
        )


class SourceBrowser(QWidget):
    """A searchable full published copy; no invented source provenance or counts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索原名或译名")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索当前采用的术语")
        ComponentStyle.apply_static(self.search, ComponentKind.INPUT)
        toolbar.addWidget(self.search, 2)
        self.scope = QComboBox(self)
        self.scope.setAccessibleName("术语使用范围")
        for label, value in (("全部范围", ""), ("整个项目", "project"), ("指定插件", "plugin")):
            self.scope.addItem(label, value)
        ComponentStyle.apply_static(self.scope, ComponentKind.INPUT)
        toolbar.addWidget(self.scope, 1)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.model = SourceTermsModel(self)
        self.filtered = _SourceFilter(self)
        self.filtered.setSourceModel(self.model)
        self.table = QTableView(self)
        self.table.setModel(self.filtered)
        self.table.setAccessibleName("当前采用的术语")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        layout.addWidget(self.table, 1)
        self.status = QLabel("尚未选用术语副本", self)
        self.status.setProperty("tbSecondary", True)
        layout.addWidget(self.status)
        self._empty_text = self.status.text()
        self.search.textChanged.connect(self._filter)
        self.scope.currentIndexChanged.connect(self._filter)

    def render(self, state) -> None:
        revision = getattr(state, "selected_revision", None) if state.enabled else None
        self.model.set_mappings(() if revision is None else revision.content.mappings)
        self._empty_text = "此副本没有术语" if revision is not None else "尚未选用术语副本"
        if not state.enabled:
            self._empty_text = state.detail
        self._filter()

    def _filter(self, *_args) -> None:
        self.filtered.query = self.search.text().strip().casefold()
        self.filtered.scope = self.scope.currentData()
        self.filtered.invalidateFilter()
        total = self.model.rowCount()
        count = self.filtered.rowCount()
        self.status.setText(
            (f"{count:,} / {total:,} 条术语" if count != total else f"{total:,} 条术语") if total else self._empty_text
        )
