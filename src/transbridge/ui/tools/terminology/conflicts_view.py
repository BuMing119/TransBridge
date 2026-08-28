"""Conflict-review page backed by a bounded application projection model."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
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

from .paged_models import KeysetPagedTableModel


class ConflictsView(QWidget):
    query_changed = pyqtSignal(str, str)
    review_requested = pyqtSignal(object)

    def __init__(self, model: KeysetPagedTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("需要你决定", self)
        title.setProperty("tbTerminologySectionTitle", True)
        intro = QLabel("逐组确认同一原名的不同译法；出现次数只作参考，不会自动替你选择。", self)
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)
        filters = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setProperty("tbComponentKind", "input")
        self.search.setPlaceholderText("搜索原名或译名")
        self.risk = QComboBox(self)
        self.risk.setProperty("tbComponentKind", "input")
        self.risk.addItem("全部风险", "all")
        self.risk.addItem("优先处理", "high")
        self.risk.addItem("一般", "medium")
        self.apply_filter = QPushButton("应用筛选", self)
        self.apply_filter.setProperty("tbComponentKind", "button")
        self.apply_filter.clicked.connect(self._emit_query)
        self.search.returnPressed.connect(self._emit_query)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.risk)
        filters.addWidget(self.apply_filter)
        layout.addLayout(filters)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(False)
        self.table.doubleClicked.connect(lambda index: self.review_requested.emit(index.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.table, 1)
        self.status = QLabel("尚无需要关注的内容；创建或更新后会在这里显示。", self)
        layout.addWidget(self.status)
        model.loading_changed.connect(self._set_loading)
        model.rowsInserted.connect(self._sync_status)
        model.modelReset.connect(self._sync_status)
        model.cursor_restarted.connect(lambda: self.status.setText("内容已更新，正在重新载入首屏…"))
        model.query_failed.connect(lambda message: self.status.setText(f"载入失败：{message}"))

    def _emit_query(self) -> None:
        self.query_changed.emit(self.search.text().strip(), str(self.risk.currentData()))

    def _set_loading(self, loading: bool) -> None:
        self.status.setText("正在载入…" if loading else self._settled_status())

    def _sync_status(self, *_args) -> None:
        self.status.setText(self._settled_status())

    def _settled_status(self) -> str:
        if self.model.rowCount() == 0:
            return "当前没有需要关注的异译。"
        return "只加载当前可见页"


__all__ = ["ConflictsView"]
