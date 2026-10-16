"""Read-only browsing of the exact terminology profile selected for this project."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .async_row_filter import AsyncRowFilter
from .filtered_rows_model import FilteredRowsModel
from .row_search import SearchDocument


class SourceTermsModel(QAbstractTableModel):
    """Expose published mappings, never the mutable profile draft."""

    HEADERS = ("原名", "方案译名", "使用范围", "匹配状态", "项目基础译名")

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
            return (
                item.original,
                item.translation,
                item.plugin_id or "整个项目",
                _state_label(getattr(item, "state", None)),
                getattr(item, "base_translation", ""),
            )[index.column()]
        return None


class SourceBrowser(QWidget):
    """A searchable full published copy; no invented source provenance or counts."""

    draft_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.description = QLabel("方案映射只有匹配已确认的项目基础术语后才会生效；此表不等于当前生效列表。", self)
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description)
        toolbar = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索原名或译名")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索全部方案映射")
        ComponentStyle.apply_static(self.search, ComponentKind.INPUT)
        toolbar.addWidget(self.search, 2)
        self.scope = QComboBox(self)
        self.scope.setAccessibleName("术语使用范围")
        for label, value in (("全部范围", ""), ("整个项目", "project"), ("指定插件", "plugin")):
            self.scope.addItem(label, value)
        ComponentStyle.apply_static(self.scope, ComponentKind.INPUT)
        toolbar.addWidget(self.scope, 1)
        self.state_filter = QComboBox(self)
        self.state_filter.setAccessibleName("方案映射状态")
        self.state_filter.addItem("全部匹配状态", "")
        for value in ("effective", "base_missing", "base_inactive", "unmapped"):
            self.state_filter.addItem(_state_label(value), value)
        toolbar.addWidget(self.state_filter)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.model = SourceTermsModel(self)
        self.filtered = FilteredRowsModel(self.model, self)
        self.table = QTableView(self)
        self.table.setModel(self.filtered)
        self.table.setAccessibleName("译名方案映射与基础术语匹配")
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
        self.status = QLabel("使用项目默认译名，没有方案映射。", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setProperty("tbSecondary", True)
        layout.addWidget(self.status)
        self._empty_text = self.status.text()
        self._search = AsyncRowFilter(self)
        self._search.matches_changed.connect(self._matches_ready)
        self._search.status_changed.connect(self._search_status)
        self.search.textChanged.connect(self._filter)
        self.scope.currentIndexChanged.connect(self._filter)
        self.state_filter.currentIndexChanged.connect(self._filter)
        self.draft_button = QPushButton("前往草稿补充或确认基础术语", self)
        self.draft_button.clicked.connect(self.draft_requested)
        layout.addWidget(self.draft_button)

    def render(self, state) -> None:
        revision = getattr(state, "selected_revision", None) if state.enabled else None
        self._empty_text = "此方案没有映射" if revision is not None else "使用项目默认译名，没有方案映射。"
        if not state.enabled:
            self._empty_text = state.detail
        self.model.set_mappings(() if revision is None else revision.content.mappings)
        self._search.set_rows(self.model.mappings, _search_document)

    def render_context(self, context) -> None:
        self._empty_text = "此方案没有映射" if context.profile_id else "使用项目默认译名，没有方案映射。"
        description = "方案映射只有匹配已确认的项目基础术语后才会生效；缺少基础术语时，可前往草稿补充并发布。"
        if context.diagnostics:
            description += "\n" + "；".join(context.diagnostics)
        self.description.setText(description)
        self.model.set_mappings(context.mapping_rows)
        self._search.set_rows(self.model.mappings, _search_document)

    def _filter(self, *_args) -> None:
        self._search.set_query(
            self.search.text(),
            (self.scope.currentData(), self.state_filter.currentData()),
            debounce=bool(_args and isinstance(_args[0], str)),
        )

    def _matches_ready(self, matches) -> None:
        selected = self.filtered.mapToSource(self.table.currentIndex())
        self.filtered.set_matches(matches)
        index = self.filtered.mapFromSource(selected)
        if not index.isValid() and self.filtered.rowCount():
            index = self.filtered.index(0, 0)
        self.table.setCurrentIndex(index)
        self._update_count()

    def _search_status(self, pending, error) -> None:
        if pending or error:
            self.status.setText(error or "正在搜索全部方案映射…")
        else:
            self._update_count()

    def _update_count(self) -> None:
        total = self.model.rowCount()
        count = self.filtered.rowCount()
        self.status.setText(
            (f"{count:,} / {total:,} 条术语" if count != total else f"{total:,} 条术语") if total else self._empty_text
        )


def _search_document(item) -> SearchDocument:
    return SearchDocument(
        (item.original, item.translation, getattr(item, "base_translation", "")),
        (item.scope_kind, getattr(item, "state", "") or ""),
    )


def _state_label(value) -> str:
    return {
        "effective": "当前生效",
        "base_missing": "缺少项目基础术语",
        "base_inactive": "基础术语未确认或已停用",
        "unmapped": "方案缺少映射",
    }.get(value, "尚未检查匹配状态")
