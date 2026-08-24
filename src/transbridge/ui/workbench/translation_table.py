"""Incrementally rendered Workbench translation table."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6 import sip
from PyQt6.QtCore import QItemSelectionModel, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from transbridge.converter.translation_entry import (
    STAGE_COLORS,
    STAGE_HIDDEN,
    STAGE_LABELS,
    STAGE_LOCKED,
    STAGE_TRANSLATED,
    TranslationEntry,
)
from transbridge.ui.workbench.filters_presenter import entry_category
from transbridge.ui.workbench.table_presenter import RenderSession

COL_MARK = 0
COL_KEY = 1
COL_ORIGINAL = 2
COL_TRANSLATION = 3
COL_CONTEXT = 4
NUM_COLUMNS = 5
ROW_BATCH_SIZE = 250
ROW_BG_GREEN = QColor("#E8F5E9")


class TranslationTable(QTableWidget):
    """QTableWidget view that owns batching, identity and locate behavior."""

    def __init__(
        self,
        *,
        on_progress: Callable[[int, int], None],
        on_batch: Callable[[], None],
        parent=None,
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

    @property
    def render_session(self) -> RenderSession:
        return self._session

    @property
    def has_pending_batch(self) -> bool:
        """Whether an owned render callback is waiting in the Qt event loop."""
        return self._batch_timer.isActive() and self._scheduled_generation is not None

    def _configure(self) -> None:
        self.setAccessibleName("翻译词条表")
        self.setAccessibleDescription("双击译文列可编辑；状态同时显示为文字，不仅使用颜色。")
        self.setHorizontalHeaderLabels(["标签数", "Key", "原文", "译文", "类型 / 状态"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_MARK, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_MARK, 32)
        header.setSectionResizeMode(COL_KEY, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_ORIGINAL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TRANSLATION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_CONTEXT, QHeaderView.ResizeMode.ResizeToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

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
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_KEY, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_CONTEXT, QHeaderView.ResizeMode.Interactive)
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
        if start == 0:
            self.resizeColumnToContents(COL_KEY)
            self.resizeColumnToContents(COL_CONTEXT)
            header.setSectionResizeMode(COL_KEY, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(COL_CONTEXT, QHeaderView.ResizeMode.Interactive)
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
        row_background = self._row_background(entry)
        stage_color = QColor(STAGE_COLORS.get(entry.stage, "#000000"))
        labels = self._entry_labels.get(entry.id, set()) if entry.id else set()

        if labels:
            first_info = self._label_library.get(next(iter(labels)), {})
            mark = QTableWidgetItem(str(len(labels)))
            mark.setForeground(QColor(first_info.get("color", "#999")))
            mark.setToolTip(
                "\n".join(
                    self._label_library[label_id]["name"] for label_id in labels if label_id in self._label_library
                )
            )
        else:
            mark = QTableWidgetItem("")
        mark.setData(Qt.ItemDataRole.UserRole, entry)
        mark.setFlags(mark.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if row_background is not None:
            mark.setBackground(row_background)
        mark.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        key = QTableWidgetItem(entry.key or "")
        key.setData(Qt.ItemDataRole.UserRole, entry)
        key.setFlags(key.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if row_background is not None:
            key.setBackground(row_background)
        key.setForeground(stage_color)

        original = QTableWidgetItem(entry.original[:80] if entry.original else "")
        original.setData(Qt.ItemDataRole.UserRole, entry)
        original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if row_background is not None:
            original.setBackground(row_background)

        translation_text = entry.translation or ""
        translation = QTableWidgetItem(translation_text[:80] if translation_text else "（无译文）")
        translation.setData(Qt.ItemDataRole.UserRole, entry)
        if row_background is not None:
            translation.setBackground(row_background)
        translation.setForeground(QColor("#4CAF50" if translation_text else "#9E9E9E"))

        stage_label = STAGE_LABELS.get(entry.stage, f"状态 {entry.stage}")
        context = QTableWidgetItem(f"{entry_category(entry)} · {stage_label}")
        context.setData(Qt.ItemDataRole.UserRole, entry)
        context.setFlags(context.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if row_background is not None:
            context.setBackground(row_background)

        self.setItem(row, COL_MARK, mark)
        self.setItem(row, COL_KEY, key)
        self.setItem(row, COL_ORIGINAL, original)
        self.setItem(row, COL_TRANSLATION, translation)
        self.setItem(row, COL_CONTEXT, context)

    @staticmethod
    def _row_background(entry: TranslationEntry) -> QColor | None:
        if entry.stage == STAGE_HIDDEN:
            return QColor("#F5F5F5")
        if entry.stage == STAGE_LOCKED:
            return QColor("#FFEBEE")
        if entry.stage >= STAGE_TRANSLATED:
            return ROW_BG_GREEN
        return None

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
        if 0 <= preferred_row < self.rowCount():
            candidate = self.item(preferred_row, COL_TRANSLATION)
            candidate_entry = None if candidate is None else candidate.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate_entry, TranslationEntry) and candidate_entry.id == entry_id:
                return preferred_row, candidate
        for row in range(self.rowCount()):
            candidate = self.item(row, COL_TRANSLATION)
            candidate_entry = None if candidate is None else candidate.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate_entry, TranslationEntry) and candidate_entry.id == entry_id:
                return row, candidate
        return -1, None
