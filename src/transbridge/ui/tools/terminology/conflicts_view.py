# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""A bounded conflict queue beside an in-place review pane."""

from PyQt6.QtCore import QSignalBlocker, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .conflict_review_pane import ConflictReviewPane
from .pagination_bar import PaginationBar


class ConflictQueueDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return
        detail = f"{len(item.variants)} 个候选译名"
        if item.status == "ignored":
            detail = "已排除此术语"
        elif item.status != "unresolved" and item.recommended_translation:
            detail = f"{'归并' if item.status == 'equivalent' else '已采用'}：{item.recommended_translation}"
        option.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        painter.save()
        role = (
            QPalette.ColorRole.HighlightedText
            if option.state & QStyle.StateFlag.State_Selected
            else QPalette.ColorRole.Text
        )
        painter.setPen(option.palette.color(role))
        rect = option.rect.adjusted(12, 8, -12, -8)
        rect.setHeight(rect.height() // 2)
        painter.setFont(option.font)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignVCenter,
            option.fontMetrics.elidedText(item.normalized_original, Qt.TextElideMode.ElideRight, rect.width()),
        )
        rect.translate(0, rect.height())
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignVCenter,
            option.fontMetrics.elidedText(detail, Qt.TextElideMode.ElideRight, rect.width()),
        )
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(260, 66)


class ConflictsView(QWidget):
    query_changed = pyqtSignal(str, str)
    review_requested = pyqtSignal(object)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self._selected_id = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QHBoxLayout()
        self.progress = QLabel("处理术语冲突", self)
        self.progress.setProperty("tbTerminologySectionTitle", True)
        self.toolbar.addWidget(self.progress, 1)
        self.undo_button = QPushButton("撤销上次决定", self)
        ComponentStyle.apply_static(self.undo_button, ComponentKind.BUTTON)
        self.undo_button.setEnabled(False)
        self.toolbar.addWidget(self.undo_button)
        root.addLayout(self.toolbar)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)
        self.queue = QWidget(self.splitter)
        left = QVBoxLayout(self.queue)
        left.setContentsMargins(0, 0, 8, 0)
        self.tabs = QTabBar(self.queue)
        for title in ("待处理", "已处理", "无需处理"):
            self.tabs.addTab(title)
        left.addWidget(self.tabs)
        self.search = QLineEdit(self.queue)
        self.search.setProperty("tbComponentKind", "input")
        self.search.setPlaceholderText("搜索术语或译名，按 Enter")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索异译原名或译名")
        left.addWidget(self.search)
        filters = QHBoxLayout()
        self.risk = QComboBox(self.queue)
        self.risk.setAccessibleName("异译风险筛选")
        for title, value in (("全部优先级", "all"), ("优先处理", "high"), ("一般", "medium")):
            self.risk.addItem(title, value)
        self.apply_filter = QPushButton("搜索", self.queue)
        self.reset_button = QPushButton("重置", self.queue)
        self.reset_button.setEnabled(False)
        for button in (self.apply_filter, self.reset_button):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        filters.addWidget(self.risk, 1)
        filters.addWidget(self.apply_filter)
        filters.addWidget(self.reset_button)
        left.addLayout(filters)
        self.table = QTableView(self.queue)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setItemDelegateForColumn(0, ConflictQueueDelegate(self.table))
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(66)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, model.columnCount()):
            self.table.hideColumn(column)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.addWidget(self.table, 1)
        self.pagination = PaginationBar(model, self.queue)
        controls = self.pagination.layout().itemAt(0).layout()
        controls.removeWidget(self.pagination.range_label)
        self.pagination.layout().addWidget(self.pagination.range_label)
        self.pagination.first_button.hide()
        self.pagination.refresh_button.setText("刷新")
        left.addWidget(self.pagination)
        self.status = QLabel(self.queue)
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        self.pane = ConflictReviewPane(self.splitter)
        self.splitter.addWidget(self.queue)
        self.splitter.addWidget(self.pane)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([350, 700])
        self.tabs.currentChanged.connect(self._emit_query)
        self.search.returnPressed.connect(self._emit_query)
        self.search.textChanged.connect(lambda text: self._emit_query() if not text else None)
        self.search.textChanged.connect(self._sync_reset)
        self.risk.currentIndexChanged.connect(self._emit_query)
        self.apply_filter.clicked.connect(self._emit_query)
        self.reset_button.clicked.connect(self.reset_filters)
        self.table.selectionModel().currentRowChanged.connect(self._selected)
        model.page_loaded.connect(self._loaded)
        model.loading_changed.connect(self._loading)
        model.query_failed.connect(lambda message: self.status.setText(f"读取失败：{message}，可刷新重试。"))

    @property
    def review_state(self):
        return ("pending", "resolved", "equivalent")[self.tabs.currentIndex()]

    def show_equivalent_results(self):
        self.tabs.setCurrentIndex(2)

    def _emit_query(self, *_args):
        self._selected_id = None
        self.reset_button.setText("重置")
        self._sync_reset()
        self.query_changed.emit(self.search.text().strip(), str(self.risk.currentData()))

    def _sync_reset(self, *_args):
        self.reset_button.setEnabled(bool(self.search.text()) or self.risk.currentData() != "all")

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        item = self.table.currentIndex().data(Qt.ItemDataRole.UserRole)
        if item is not None and self.pane.details is None:
            self.review_requested.emit(item)

    def reset_filters(self):
        with QSignalBlocker(self.search), QSignalBlocker(self.risk):
            self.search.clear()
            self.risk.setCurrentIndex(0)
        self._emit_query()

    def _selected(self, index, _previous):
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is not None:
            self._selected_id = item.conflict_group_id
            self.review_requested.emit(item)

    def _loaded(self, *_args):
        if self.model.rowCount():
            row = next(
                (
                    row
                    for row in range(self.model.rowCount())
                    if self.model.index(row, 0).data(Qt.ItemDataRole.UserRole).conflict_group_id == self._selected_id
                ),
                0,
            )
            self.table.selectRow(row)
            query = self.model._query
            anchored = query is not None and getattr(query.snapshot_ref.filters, "start_at", None)
            self.status.setText("从上次位置继续 · 完整队列可查看前面的术语" if anchored else "")
        else:
            self.status.setText("当前筛选下没有术语。")
            self.review_requested.emit(None)

    def _loading(self, loading):
        self.table.setEnabled(not loading)
        if loading:
            self.status.setText("正在读取…")

    def next_conflict(self):
        row = self.table.currentIndex().row()
        index = self.model.index(row + 1, 0)
        return index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None

    def select_next(self):
        if self.next_conflict() is not None:
            self.table.selectRow(self.table.currentIndex().row() + 1)
        elif self.model.pagination.can_next:
            self._selected_id = None
            self.model.navigate_next()
        elif self.model.rowCount():
            self.table.selectRow(0)


__all__ = ["ConflictsView"]
