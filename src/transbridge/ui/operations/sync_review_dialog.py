"""Content-first review; decisions remain staged until the main sync confirmation."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import configure_accessible_widget
from transbridge.ui.foundation.components import configure_dialog

from .plan_view import OperationKind
from .sync_review_detail import SyncReviewDetail
from .sync_review_direction import sync_review_direction
from .sync_review_list import SyncReviewListDelegate
from .sync_review_model import SyncReviewFilter, SyncReviewModel
from .sync_review_view import SyncReviewRow


class SyncReviewDialog(QDialog):
    """Browse translations and stage explicit choices without writing data."""

    def __init__(self, rows: tuple[SyncReviewRow, ...], parent=None, *, kind=OperationKind.DOWNLOAD) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("确认这几条翻译")
        self.resize(1120, 820)
        self._direction = sync_review_direction(kind)
        self._model = SyncReviewModel(rows, self, kind=kind)
        self._proxy = SyncReviewFilter(self)
        self._proxy.setSourceModel(self._model)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(16)
        heading = QLabel("确认这几条翻译", self)
        font = heading.font()
        font.setPointSizeF(font.pointSizeF() + 5)
        font.setBold(True)
        heading.setFont(font)
        root.addWidget(heading)
        notice = QLabel(self._direction.notice, self)
        notice.setWordWrap(True)
        root.addWidget(notice)
        body = QSplitter(Qt.Orientation.Horizontal, self)
        sidebar = QWidget(body)
        sidebar.setMinimumWidth(220)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(0, 0, 8, 0)
        navigation.setSpacing(10)
        self._summary = QLabel(sidebar)
        navigation.addWidget(self._summary)
        self._search = QLineEdit(sidebar)
        self._search.setPlaceholderText("搜索原文、译文或使用场景")
        configure_accessible_widget(self._search, name="搜索需要确认的翻译")
        navigation.addWidget(self._search)
        self._state_filter = QComboBox(sidebar)
        for label, value in (("全部翻译", ""), ("还没决定", "pending"), ("已选择", "decided")):
            self._state_filter.addItem(label, value)
        configure_accessible_widget(self._state_filter, name="按是否已选择筛选")
        navigation.addWidget(self._state_filter)
        more = QPushButton("按问题筛选", sidebar)
        more.setCheckable(True)
        more.setAutoDefault(False)
        navigation.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        self._reason_filter = QComboBox(sidebar)
        self._reason_filter.addItem("所有问题", "")
        self._reason_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._reason_filter.setMinimumContentsLength(12)
        for reason in dict.fromkeys(row.reason for row in rows):
            self._reason_filter.addItem(reason, reason)
        self._reason_filter.hide()
        more.toggled.connect(self._reason_filter.setVisible)
        navigation.addWidget(self._reason_filter)
        self._table = QTableView(sidebar)
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().hide()
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4):
            self._table.hideColumn(column)
        self._table.setItemDelegate(SyncReviewListDelegate(self._table))
        self._table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() * 3 + 28)
        configure_accessible_widget(self._table, name="需要确认的翻译", description="按住 Ctrl 可选择多条，一起跳过")
        navigation.addWidget(self._table, 1)
        self._select_all = QPushButton("全选当前列表", sidebar)
        self._select_all.setAutoDefault(False)
        navigation.addWidget(self._select_all)
        detail_scroll = QScrollArea(body)
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail = SyncReviewDetail(self._direction)
        detail_scroll.setWidget(self._detail)
        body.setSizes([270, 790])
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setChildrenCollapsible(False)
        root.addWidget(body, 1)
        footer = QHBoxLayout()
        self._footer_note = QLabel(f"选择尚未执行。返回后，确认{self._direction.verb}才会生效。", self)
        self._footer_note.setWordWrap(True)
        footer.addWidget(self._footer_note, 1)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply, self
        )
        cancel = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setAutoDefault(False)
        cancel.setDefault(False)
        apply = self._buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply.setText(f"完成选择，返回{self._direction.verb}")
        apply.setAutoDefault(False)
        self._buttons.rejected.connect(self.reject)
        apply.clicked.connect(self.accept)
        footer.addWidget(self._buttons)
        root.addLayout(footer)
        self._search.textChanged.connect(self._filter_changed)
        self._reason_filter.currentIndexChanged.connect(self._filter_changed)
        self._state_filter.currentIndexChanged.connect(self._filter_changed)
        self._table.selectionModel().selectionChanged.connect(self._show_selection)
        self._select_all.clicked.connect(self._table.selectAll)
        self._detail.keep.clicked.connect(lambda: self._set_selected(self._direction.keep_choice))
        self._detail.undo.clicked.connect(lambda: self._set_selected(""))
        self._detail.use_remote.clicked.connect(self._choose_candidate)
        self._detail.next.clicked.connect(self._next_pending)
        if rows:
            self._table.selectRow(0)
        self._show_selection()

    def decisions(self) -> dict[str, str]:
        return self._model.decisions()

    def _selected_rows(self) -> tuple[int, ...]:
        return tuple(self._proxy.mapToSource(index).row() for index in self._table.selectionModel().selectedRows())

    def _filter_changed(self, *_args) -> None:
        self._proxy.set_filters(
            search=self._search.text(),
            reason=self._reason_filter.currentData(),
            status=self._state_filter.currentData(),
        )
        self._show_selection()

    def _show_selection(self, *_args) -> None:
        selected = self._selected_rows()
        total = self._model.rowCount()
        self._summary.setText(f"{total:,} 条需要确认 · {len(self.decisions()):,} 条已选择")
        self._select_all.setEnabled(self._proxy.rowCount() > 0)
        row = self._model.rows[selected[0]] if len(selected) == 1 else None
        decision = self._model.decision(selected[0]) if row else ""
        self._detail.show_row(row, decision, len(selected))
        self._detail.undo.setEnabled(any(self._model.decision(index) for index in selected))
        self._detail.next.setEnabled(bool(self._pending_rows(excluding=selected)))

    def _pending_rows(self, *, excluding: tuple[int, ...]) -> list[int]:
        excluded = set(excluding)
        return [
            index
            for index in range(self._proxy.rowCount())
            if (source := self._proxy.mapToSource(self._proxy.index(index, 0)).row()) not in excluded
            and not self._model.decision(source)
        ]

    def _next_pending(self) -> None:
        pending = self._pending_rows(excluding=self._selected_rows())
        if pending:
            current = self._table.currentIndex().row()
            target = next((index for index in pending if index > current), pending[0])
            self._table.selectRow(target)
            self._table.scrollTo(self._proxy.index(target, 1))

    def _set_selected(self, decision: str) -> None:
        selected = self._selected_rows()
        with QSignalBlocker(self._table.selectionModel()):
            self._model.set_decision(selected, decision)
        self._show_selection()

    def _choose_candidate(self) -> None:
        if self._detail.use_remote.isEnabled() and self._detail.candidates.currentData():
            self._set_selected(self._detail.candidates.currentData())
