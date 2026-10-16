"""Read-only, fully pageable comparisons for synchronization review and results."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .sync_projection import SyncDetailRow, outcome_rows, plan_rows, proposal_rows

__all__ = ["SyncDetailRow", "SyncDetailsTable", "outcome_rows", "plan_rows", "proposal_rows"]


class SyncDetailsTable(QFrame):
    """A comparison page with complete selected-row text and item navigation."""

    page_requested = pyqtSignal(int)
    item_requested = pyqtSignal(str)

    def __init__(self, title: str, headers: tuple[str, ...], parent=None, *, locate: bool = False) -> None:
        super().__init__(parent)
        self._headers = headers
        self._rows: tuple[SyncDetailRow, ...] | None = None
        self._projection = None
        self._page: tuple[SyncDetailRow, ...] = ()
        self._offset = 0
        self._busy = False
        self._page_size = 50
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title, self))
        self.table = QTableWidget(0, len(headers), self)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setAccessibleName(title)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(160)
        layout.addWidget(self.table)
        self.detail = QPlainTextEdit(self)
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName(title + "完整条目详情")
        self.detail.setMaximumHeight(150)
        layout.addWidget(self.detail)
        controls = QHBoxLayout()
        self.previous_button = QPushButton("上一页", self)
        self.next_button = QPushButton("下一页", self)
        self.range_label = QLabel(self)
        self.locate_button = QPushButton("定位此项并处理", self)
        self.locate_button.setVisible(locate)
        for widget in (self.previous_button, self.next_button, self.range_label, self.locate_button):
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.previous_button.clicked.connect(lambda: self._move(-1))
        self.next_button.clicked.connect(lambda: self._move(1))
        self.locate_button.clicked.connect(self._locate)
        self.table.itemSelectionChanged.connect(self._selection_changed)

    def set_rows(self, rows: tuple[SyncDetailRow, ...], *, busy: bool = False) -> None:
        self._projection = None
        changed = rows != self._rows
        self._rows = rows
        if changed:
            self._offset = 0
        self._show_internal(busy)

    def set_projection(self, projection, *, busy: bool = False) -> None:
        if projection is self._projection and self._rows is None:
            # No new data or formatting; keep selection, scroll and detail text.
            self._busy = busy
            total = 0 if projection is None else projection.total
            self.previous_button.setEnabled(self._offset > 0 and not busy)
            self.next_button.setEnabled(self._offset + self._page_size < total and not busy)
            self.locate_button.setEnabled(bool(self._page) and not busy)
            return
        self._projection, self._rows, self._offset = projection, None, 0
        self._show_projection(busy)

    def _show_projection(self, busy):
        projection = self._projection
        total = 0 if projection is None else projection.total
        rows = () if projection is None else projection.page(self._offset, self._page_size)
        self._render(rows, self._offset, total, self._offset > 0, self._offset + self._page_size < total, busy)

    def set_page(self, rows, *, offset: int, total: int, previous: bool, following: bool, busy: bool = False) -> None:
        self._rows = self._projection = None
        self._render(rows, offset, total, previous, following, busy)

    def _show_internal(self, busy: bool) -> None:
        assert self._rows is not None
        self._render(
            self._rows[self._offset : self._offset + self._page_size],
            self._offset,
            len(self._rows),
            self._offset > 0,
            self._offset + self._page_size < len(self._rows),
            busy,
        )

    def _render(self, rows, offset, total, previous, following, busy) -> None:
        self._page, self._offset, self._busy = tuple(rows), offset, busy
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            for column, value in enumerate(item.cells):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
        self.previous_button.setEnabled(previous and not busy)
        self.next_button.setEnabled(following and not busy)
        self.range_label.setText(f"{offset + 1 if rows else 0}-{offset + len(rows)} / {total}")
        self.locate_button.setEnabled(bool(rows) and not busy)
        if rows:
            self.table.selectRow(0)
        else:
            self.detail.clear()
        self._selection_changed()

    def _move(self, direction: int) -> None:
        if self._projection is not None:
            self._offset = max(0, min(self._offset + direction * self._page_size, max(0, self._projection.total - 1)))
            self._show_projection(self._busy)
            return
        if self._rows is None:
            self.page_requested.emit(direction)
            return
        self._offset = max(0, min(self._offset + direction * self._page_size, max(0, len(self._rows) - 1)))
        self._show_internal(self._busy)

    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._page):
            self.detail.setPlainText(
                "\n\n".join(
                    f"{header}：\n{value}" for header, value in zip(self._headers, self._page[row].cells, strict=True)
                )
            )

    def _locate(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._page):
            self.item_requested.emit(self._page[row].item_id)
