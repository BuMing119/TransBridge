"""Incrementally rendered Workbench translation table."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6 import sip
from PyQt6.QtCore import QItemSelectionModel, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from transbridge.converter.translation_entry import STAGE_LABELS, TranslationEntry
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.workbench.filters_presenter import entry_category
from transbridge.ui.workbench.table_presenter import RenderSession

from .translation_table_columns import (
    COL_CHECK,
    COL_CONTEXT,
    COL_INDEX,
    COL_KEY,
    COL_MARK,
    COL_ORIGINAL,
    COL_TRANSLATION,
    NUM_COLUMNS,
)
from .translation_table_delegate import TranslationThemeDelegate

ROW_BATCH_SIZE = 250


class TranslationTable(QTableWidget):
    """QTableWidget view that owns batching, identity and locate behavior."""

    def __init__(
        self,
        *,
        on_progress: Callable[[int, int], None],
        on_batch: Callable[[], None],
        parent=None,
        theme_view: ThemeView | None = None,
    ) -> None:
        super().__init__(0, NUM_COLUMNS, parent)
        self._on_progress = on_progress
        self._on_batch = on_batch
        self._session = RenderSession(0, None, ())
        self._entry_labels: Mapping[str, set[str]] = {}
        self._label_library: Mapping[str, Mapping[str, str]] = {}
        self._pending_entry_id: str | None = None
        self._pending_selected_entry_ids: set[str] = set()
        self._pending_scroll_entry_id: str | None = None
        self._pending_scroll_value = 0
        self._closed = False
        self._scheduled_generation: int | None = None
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._drain_scheduled_batch)
        self._configure()
        ComponentStyle.apply_static(self, ComponentKind.TABLE)
        self._theme_delegate = TranslationThemeDelegate(self, theme_view)
        self.setItemDelegate(self._theme_delegate)

    @property
    def render_session(self) -> RenderSession:
        return self._session

    @property
    def has_pending_batch(self) -> bool:
        """Whether an owned render callback is waiting in the Qt event loop."""
        return self._batch_timer.isActive() and self._scheduled_generation is not None

    @property
    def theme_revision(self) -> int:
        return self._theme_delegate.revision

    def _configure(self) -> None:
        self.setAccessibleName("翻译词条表")
        self.setAccessibleDescription("双击译文列可编辑；状态同时显示为文字，不仅使用颜色。")
        self.setHorizontalHeaderLabels(["", "#", "标签", "Key", "原文", "译文", "类型 / 状态"])
        header = self.horizontalHeader()
        header.setMinimumSectionSize(28)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_CHECK, 30)
        header.setSectionResizeMode(COL_INDEX, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(COL_INDEX, 42)
        header.setSectionResizeMode(COL_MARK, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(COL_MARK, 48)
        header.setSectionResizeMode(COL_KEY, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(COL_KEY, 300)
        header.setSectionResizeMode(COL_ORIGINAL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TRANSLATION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_CONTEXT, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_CONTEXT, 150)
        self.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(30)
        self.setMouseTracking(True)
        self.itemChanged.connect(self._on_check_state_changed)
        self.selectionModel().selectionChanged.connect(self._sync_check_selection)

    def start_render(
        self,
        session: RenderSession,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping[str, str]],
    ) -> None:
        self._capture_view_state()
        self._batch_timer.stop()
        self._scheduled_generation = None
        self._closed = False
        self._session = session
        self._entry_labels = entry_labels
        self._label_library = label_library
        self.clearContents()
        self.setRowCount(0)
        self._on_progress(0, len(session.entries))
        self.append_batch(session.generation)

    def append_batch(self, generation: int) -> None:
        if not self._can_render(generation):
            return
        session = self._session
        start = self.rowCount()
        end = min(start + ROW_BATCH_SIZE, len(session.entries))
        self.setUpdatesEnabled(False)
        self.blockSignals(True)
        self.setRowCount(end)
        for row in range(start, end):
            self._render_row(row, session.entries[row])
        self.setUpdatesEnabled(True)
        self.blockSignals(False)
        self._on_progress(end, len(session.entries))
        self._on_batch()
        if not self._can_render(generation):
            return
        self._select_pending(start, end)
        self._restore_view_state(start, end)
        if end < len(session.entries):
            self._scheduled_generation = generation
            self._batch_timer.start(0)
        elif self._pending_scroll_entry_id is not None:
            self.verticalScrollBar().setValue(min(self._pending_scroll_value, self.verticalScrollBar().maximum()))
            self._pending_scroll_entry_id = None

    def _drain_scheduled_batch(self) -> None:
        generation = self._scheduled_generation
        self._scheduled_generation = None
        if generation is None or not self._can_render(generation):
            return
        self.append_batch(generation)

    def _can_render(self, generation: int) -> bool:
        return not self._closed and not sip.isdeleted(self) and generation == self._session.generation

    def close_rendering(self) -> None:
        """Idempotently detach every queued render callback owned by this view."""
        if self._closed:
            return
        self._closed = True
        self._batch_timer.stop()
        self._scheduled_generation = None
        self._pending_entry_id = None
        self._pending_selected_entry_ids.clear()
        self._pending_scroll_entry_id = None
        self._pending_scroll_value = 0
        self._entry_labels = {}
        self._label_library = {}
        self._session = RenderSession(self._session.generation + 1, None, ())

    def _render_row(self, row: int, entry: TranslationEntry) -> None:
        labels = self._entry_labels.get(entry.id, set()) if entry.id else set()

        check = QTableWidgetItem("")
        check.setData(Qt.ItemDataRole.UserRole, entry)
        check.setFlags(
            (check.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            & ~Qt.ItemFlag.ItemIsEditable
        )
        check.setCheckState(Qt.CheckState.Unchecked)
        check.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        index_item = QTableWidgetItem(str(row + 1))
        index_item.setData(Qt.ItemDataRole.UserRole, entry)
        index_item.setFlags(index_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        if labels:
            mark = QTableWidgetItem(str(len(labels)))
            mark.setToolTip(
                "\n".join(
                    self._label_library[label_id]["name"] for label_id in labels if label_id in self._label_library
                )
            )
        else:
            mark = QTableWidgetItem("")
        mark.setData(Qt.ItemDataRole.UserRole, entry)
        mark.setFlags(mark.flags() & ~Qt.ItemFlag.ItemIsEditable)
        mark.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        key = QTableWidgetItem(entry.key or "")
        key.setData(Qt.ItemDataRole.UserRole, entry)
        key.setFlags(key.flags() & ~Qt.ItemFlag.ItemIsEditable)

        original = QTableWidgetItem(entry.original[:80] if entry.original else "")
        original.setData(Qt.ItemDataRole.UserRole, entry)
        original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)

        translation_text = entry.translation or ""
        translation = QTableWidgetItem(translation_text[:80] if translation_text else "（无译文）")
        translation.setData(Qt.ItemDataRole.UserRole, entry)

        stage_label = STAGE_LABELS.get(entry.stage, f"状态 {entry.stage}")
        context = QTableWidgetItem(f"{entry_category(entry)} · {stage_label}")
        context.setData(Qt.ItemDataRole.UserRole, entry)
        context.setFlags(context.flags() & ~Qt.ItemFlag.ItemIsEditable)

        self.setItem(row, COL_CHECK, check)
        self.setItem(row, COL_INDEX, index_item)
        self.setItem(row, COL_MARK, mark)
        self.setItem(row, COL_KEY, key)
        self.setItem(row, COL_ORIGINAL, original)
        self.setItem(row, COL_TRANSLATION, translation)
        self.setItem(row, COL_CONTEXT, context)

    def refresh_row_visuals(self, row: int) -> None:
        """Repaint one materialized row without changing item identity or data roles."""
        if 0 <= row < self.rowCount():
            first = self.model().index(row, 0)
            last = self.model().index(row, NUM_COLUMNS - 1)
            self.viewport().update(self.visualRect(first).united(self.visualRect(last)))

    def update_rendered_entry(self, entry: TranslationEntry, preferred_row: int = -1) -> int:
        """Synchronize one materialized row without restarting its render session."""

        row = self.find_entry_row(preferred_row, entry.id)
        if row < 0:
            return -1
        self.blockSignals(True)
        try:
            translation = self.item(row, COL_TRANSLATION)
            if translation is not None:
                text = entry.translation or ""
                translation.setText(text[:80] if text else "（无译文）")
                translation.setData(Qt.ItemDataRole.UserRole, entry)
            context = self.item(row, COL_CONTEXT)
            if context is not None:
                stage_label = STAGE_LABELS.get(entry.stage, f"状态 {entry.stage}")
                context.setText(f"{entry_category(entry)} · {stage_label}")
                context.setData(Qt.ItemDataRole.UserRole, entry)
            for column in (COL_CHECK, COL_INDEX, COL_MARK, COL_KEY, COL_ORIGINAL):
                item = self.item(row, column)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, entry)
        finally:
            self.blockSignals(False)
        self.refresh_row_visuals(row)
        return row

    def _on_check_state_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_CHECK:
            return
        flags = (
            QItemSelectionModel.SelectionFlag.Select
            if item.checkState() == Qt.CheckState.Checked
            else QItemSelectionModel.SelectionFlag.Deselect
        )
        self.selectionModel().select(
            self.model().index(item.row(), COL_KEY), flags | QItemSelectionModel.SelectionFlag.Rows
        )

    def _sync_check_selection(self, selected, deselected) -> None:
        self.blockSignals(True)
        try:
            for selection, state in ((selected, Qt.CheckState.Checked), (deselected, Qt.CheckState.Unchecked)):
                rows: set[int] = set()
                for index in selection.indexes():
                    rows.add(index.row())
                for row in rows:
                    item = self.item(row, COL_CHECK)
                    if item is not None:
                        item.setCheckState(state)
        finally:
            self.blockSignals(False)

    def find_entry_row(self, preferred_row: int, entry_id: str) -> int:
        if 0 <= preferred_row < self.rowCount():
            candidate = self.item(preferred_row, COL_KEY)
            candidate_entry = None if candidate is None else candidate.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate_entry, TranslationEntry) and candidate_entry.id == entry_id:
                return preferred_row
        for row in range(self.rowCount()):
            candidate = self.item(row, COL_KEY)
            candidate_entry = None if candidate is None else candidate.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate_entry, TranslationEntry) and candidate_entry.id == entry_id:
                return row
        return -1

    def locate_entry(self, entry_id: str) -> None:
        self._pending_entry_id = entry_id
        self._select_pending(0, self.rowCount())

    def selected_entry_ids(self) -> tuple[str, ...]:
        selected: list[str] = []
        for index in self.selectionModel().selectedRows(COL_KEY):
            item = self.item(index.row(), COL_KEY)
            entry = None if item is None else item.data(Qt.ItemDataRole.UserRole)
            if isinstance(entry, TranslationEntry) and entry.id:
                selected.append(entry.id)
        return tuple(selected)

    def _capture_view_state(self) -> None:
        if not self._session.entries or not self.rowCount():
            return
        self._pending_selected_entry_ids = set(self.selected_entry_ids())
        self._pending_scroll_value = self.verticalScrollBar().value()
        top_row = self.rowAt(0)
        if top_row < 0:
            top_row = min(self._pending_scroll_value, self.rowCount() - 1)
        item = self.item(top_row, COL_KEY)
        entry = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        self._pending_scroll_entry_id = entry.id if isinstance(entry, TranslationEntry) and entry.id else None

    def _restore_view_state(self, start: int, end: int) -> None:
        entries = self._session.entries
        selection = self.selectionModel()
        for row in range(start, min(end, len(entries))):
            entry_id = entries[row].id
            if entry_id in self._pending_selected_entry_ids:
                selection.select(
                    self.model().index(row, COL_KEY),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
                self._pending_selected_entry_ids.discard(entry_id)
            if entry_id == self._pending_scroll_entry_id:
                item = self.item(row, COL_KEY)
                self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
                self._pending_scroll_entry_id = None

    def _select_pending(self, start: int, end: int) -> None:
        target = self._pending_entry_id
        if target is None:
            return
        entries = self._session.entries
        for row in range(start, min(end, len(entries))):
            if entries[row].id != target:
                continue
            item = self.item(row, COL_KEY)
            self.selectRow(row)
            self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._pending_entry_id = None
            return

    def find_translation_item(
        self,
        preferred_row: int,
        entry_id: str,
    ) -> tuple[int, QTableWidgetItem | None]:
        row = self.find_entry_row(preferred_row, entry_id)
        return (row, None) if row < 0 else (row, self.item(row, COL_TRANSLATION))
