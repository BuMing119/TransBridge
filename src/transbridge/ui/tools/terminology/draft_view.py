"""Manual-adjustment page for terminology drafts."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from transbridge.application.terminology.models import DecisionStatus

from .paged_models import KeysetPagedTableModel
from .pagination_bar import PaginationBar
from .term_filters import TermFilterBar


class DraftView(QWidget):
    query_changed = pyqtSignal(object)
    add_requested = pyqtSignal()
    edit_requested = pyqtSignal(object)
    suppress_requested = pyqtSignal(object)
    batch_requested = pyqtSignal(object, str)

    def __init__(self, model: KeysetPagedTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.filters = TermFilterBar(self, draft=True)
        self.filters.changed.connect(self.query_changed)
        layout.addWidget(self.filters)
        self.add_button = QPushButton("＋  新增术语", self)
        self.add_button.setProperty("tbComponentKind", "button")
        self.row_actions = QWidget(self)
        controls = QHBoxLayout(self.row_actions)
        controls.setContentsMargins(0, 0, 0, 0)
        self.selection_label = QLabel(self.row_actions)
        controls.addWidget(self.selection_label)
        self.edit_button = QPushButton("编辑词条", self.row_actions)
        self.confirm_button = QPushButton("确认当前译名", self.row_actions)
        self.suppress_button = QPushButton("停用", self.row_actions)
        self.reenable_button = QPushButton("启用", self.row_actions)
        self.add_button.clicked.connect(self.add_requested)
        self.edit_button.clicked.connect(lambda: self._emit_selected(self.edit_requested))
        self.confirm_button.clicked.connect(lambda: self._emit_batch("confirm"))
        self.suppress_button.clicked.connect(self._suppress_selection)
        self.reenable_button.clicked.connect(lambda: self._emit_batch("reenable"))
        for button in (self.edit_button, self.confirm_button, self.suppress_button, self.reenable_button):
            button.setProperty("tbComponentKind", "button")
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addWidget(self.row_actions)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAccessibleName("草稿术语；可用 Ctrl 或 Shift 多选，Ctrl+A 选择本页")
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
        if isinstance(model, KeysetPagedTableModel):
            self.pagination = PaginationBar(model, self)
            layout.addWidget(self.pagination)
        self._update_actions()

    def _update_actions(self, *_args) -> None:
        items = self.selected_items()
        count = len(items)
        self.row_actions.setVisible(count > 0)
        self.selection_label.setText(f"本页已选 {count} 条")
        self.edit_button.setEnabled(count == 1)
        self.edit_button.setVisible(count == 1)
        self.confirm_button.setText(f"确认当前译名（{count}）")
        unresolved = any(getattr(item, "status", None) is DecisionStatus.UNRESOLVED for item in items)
        has_suppressed = any(getattr(item, "suppressed", False) for item in items)
        self.confirm_button.setEnabled(count > 0 and not unresolved and not has_suppressed)
        self.confirm_button.setToolTip(
            "未决定的异译需先在“异译审核”中选择。" if unresolved else "确认每条词条现有的译名。"
        )
        if has_suppressed:
            self.confirm_button.setToolTip("已停用术语需先使用“启用”。")
        single_suppressed = count == 1 and getattr(items[0], "suppressed", False)
        self.suppress_button.setText("启用" if single_suppressed else (f"停用（{count}）" if count > 1 else "停用"))
        self.suppress_button.setEnabled(count > 0 and not (single_suppressed and unresolved))
        self.reenable_button.setText(f"启用（{count}）")
        self.reenable_button.setVisible(count > 1)
        self.reenable_button.setEnabled(count > 1 and not unresolved)

    def selected_items(self) -> tuple:
        return tuple(
            index.data(Qt.ItemDataRole.UserRole)
            for index in sorted(self.table.selectionModel().selectedRows(), key=lambda index: index.row())
        )

    def _suppress_selection(self) -> None:
        if len(self.selected_items()) == 1:
            self._emit_selected(self.suppress_requested)
        else:
            self._emit_batch("suppress")

    def _emit_batch(self, operation: str) -> None:
        items = self.selected_items()
        if items:
            self.batch_requested.emit(items, operation)

    def _emit_selected(self, signal) -> None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) == 1:
            signal.emit(rows[0].data(Qt.ItemDataRole.UserRole))


__all__ = ["DraftView"]
