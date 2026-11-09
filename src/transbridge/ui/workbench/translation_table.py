# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Lazy Workbench table view with stable translation identity and selection."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.project_rows import ProjectRowsCollection

from .resident_rows import ResidentTableRows
from .table_presenter import RenderSession
from .translation_model import TranslationTableModel
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
from .translation_table_sorting import TranslationTableSorting


class TranslationTable(QTableView):
    """Own view interaction while one model supplies all cell values lazily."""

    entry_edit_requested = pyqtSignal(object)
    translation_edit_requested = pyqtSignal(object, str, int)
    scope_sort_requested = pyqtSignal(object, bool)
    scope_select_all_requested = pyqtSignal()

    def __init__(
        self,
        *,
        on_progress: Callable[[int, int], None],
        on_batch: Callable[[], None],
        parent=None,
        theme_view: ThemeView | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_progress, self._on_batch = on_progress, on_batch
        self._session = RenderSession(0, None, ())
        self._display_order: tuple[int, ...] = ()
        self._label_keys = ()
        self._entry_rows: dict[str, int] = {}
        self._selected_logical_ids: dict[int, str] = {}
        self._entry_labels: Mapping[str, set[str]] = {}
        self._label_library: Mapping[str, Mapping[str, str]] = {}
        self._closed = False
        self._editable_entry_keys = frozenset()
        self._translation_model = TranslationTableModel(self, is_row_selected=self._is_row_selected)
        self.setModel(self._translation_model)
        self._translation_model.translation_edit_requested.connect(self.translation_edit_requested)
        self._translation_model.check_state_requested.connect(self._on_check_state_requested)
        self.selectionModel().selectionChanged.connect(self._translation_model.selection_changed)
        self._configure()
        self._sorting = TranslationTableSorting(self, self._sort_display)
        ComponentStyle.apply_static(self, ComponentKind.TABLE)
        self._theme_delegate = TranslationThemeDelegate(self, theme_view)
        self.setItemDelegate(self._theme_delegate)

    @property
    def render_session(self) -> RenderSession:
        return self._session

    @property
    def has_pending_batch(self) -> bool:
        """Compatibility observation: model rows are available immediately."""
        return False

    @property
    def theme_revision(self) -> int:
        return self._theme_delegate.revision

    @property
    def terminology_profile_active(self) -> bool:
        return self._translation_model.profile_label is not None

    def rowCount(self) -> int:  # noqa: N802 - view convenience matching Qt naming
        return self._translation_model.rowCount()

    def _configure(self) -> None:
        self.setAccessibleName("翻译词条表")
        self.setAccessibleDescription(
            "点击表头仅改变显示顺序；双击词条（复选框除外）弹出编辑窗口；F2 支持译文行内编辑。"
        )
        header = self.horizontalHeader()
        header.setMinimumSectionSize(28)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_CHECK, 30)
        header.setSectionResizeMode(COL_INDEX, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(COL_INDEX, 42)
        header.setSectionResizeMode(COL_MARK, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(COL_MARK, 48)
        header.setSectionResizeMode(COL_KEY, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(COL_KEY, 180)
        header.setSectionResizeMode(COL_ORIGINAL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TRANSLATION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_CONTEXT, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_CONTEXT, 150)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(30)
        self.setMouseTracking(True)

    def set_editable_entry_keys(self, keys) -> None:
        # A collection already supplies exact identity membership. Reuse it so
        # enabling popup editing does not build another full set during restore.
        self._editable_entry_keys = (
            keys if isinstance(keys, (TranslationEntryCollection, ProjectRowsCollection)) else frozenset(keys)
        )

    def set_terminology_projection(
        self, values: Mapping[str, object], *, profile_label: str | None, pending: bool = False, error: str = ""
    ) -> None:
        self._translation_model.set_terminology_projection(
            values, profile_label=profile_label, pending=pending, error=error
        )

    def entry_at_row(self, row: int) -> TranslationEntry | None:
        return self._translation_model.entry_at_row(row)

    def activate_entry_editor(self, index: QModelIndex) -> bool:
        if index.model() is not self.model():
            return False
        entry = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if (
            index.column() != COL_CHECK
            and not self.terminology_profile_active
            and isinstance(entry, TranslationEntry)
            and entry.identity in self._editable_entry_keys
        ):
            self.entry_edit_requested.emit(entry.identity)
            return True
        return False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        # A reset between the clicks invalidates Qt's remembered pressed index.
        # Resolve the current index before the base view can start inline editing.
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and self.activate_entry_editor(index):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def start_render(self, session, entry_labels, label_library, *, preserve_scroll=True) -> None:
        selected, current, top, scroll = self._capture_view_state()
        self._selected_logical_ids.clear()
        self._closed = False
        self._session = session
        self._entry_labels, self._label_library = entry_labels, label_library
        order = self._sorting.order(session.entries, entry_labels)
        source_keys = self._sorting.source_keys
        label_keys = tuple(entry_label_key(entry) for entry in session.entries)
        if label_keys != self._label_keys or order != self._display_order:
            self._entry_rows = {label_keys[source_row]: row for row, source_row in enumerate(order)}
        self._label_keys = label_keys
        self._display_order = order
        reset = self._translation_model.install(session, order, entry_labels, label_library, source_keys=source_keys)
        if reset:
            self._restore_view_state(
                selected, current, top if preserve_scroll else None, scroll if preserve_scroll else 0
            )
        total = len(session.entries)
        self._on_progress(total, total)
        self._on_batch()

    def start_logical_range(self, session, total, rows, entry_labels, label_library) -> None:
        self._closed = False
        model = self._translation_model
        if model.logical_count != total or session.content_scope != self._session.content_scope:
            self._selected_logical_ids.clear()
        elif not isinstance(model.logical_rows, ResidentTableRows):
            self._remember_logical_selection()
        editing_row = None
        if (
            self.state() == QAbstractItemView.State.EditingState
            and session.content_scope == self._session.content_scope
        ):
            editing_row = self.currentIndex().row()
        self._session = session
        self._entry_labels, self._label_library = entry_labels, label_library
        self._entry_rows = (
            rows.positions
            if isinstance(rows, ResidentTableRows)
            else {entry_label_key(entry): row for row, entry in rows.items()}
        )
        self._label_keys, self._display_order = (), ()
        if model.install_logical(session, total, rows, entry_labels, label_library, editing_row=editing_row):
            self.scrollToTop()
        self._on_progress(total, total)
        self._on_batch()

    def replace_session(self, session: RenderSession) -> None:
        """Install replacement entries while retaining all existing Qt indexes."""
        self._translation_model.replace_session(session)
        self._session = session

    def set_default_entry_order(self, keys, *, ranks=None, source_keys=None, source_rows=None, render=True) -> None:
        """Change display indices while retaining current source objects and intent."""
        self._sorting.set_default_order(keys, ranks=ranks, source_keys=source_keys, source_rows=source_rows)
        self._translation_model.default_ranks = self._sorting.default_ranks
        if render and not self._closed and self._translation_model.logical_count is None:
            self.start_render(self._session, self._entry_labels, self._label_library)

    def _sort_display(self) -> None:
        if self._translation_model.logical_count is None:
            self.start_render(self._session, self._entry_labels, self._label_library, preserve_scroll=False)
            self.scrollToTop()
        self.scope_sort_requested.emit(self._sorting._column, self._sorting._descending)

    def close_rendering(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._entry_labels, self._label_library = {}, {}
        self._display_order = ()
        self._label_keys = ()
        self._sorting.clear()
        self._translation_model.default_ranks = {}
        self._entry_rows = {}
        self._selected_logical_ids.clear()
        self._session = RenderSession(self._session.generation + 1, None, ())
        self._translation_model.install(self._session, (), {}, {})
        self._translation_model.set_terminology_projection({}, profile_label=None)

    def refresh_row_visuals(self, row: int) -> None:
        if 0 <= row < self.rowCount():
            first = self.model().index(row, 0)
            last = self.model().index(row, NUM_COLUMNS - 1)
            self.viewport().update(self.visualRect(first).united(self.visualRect(last)))

    def update_rendered_entry(self, entry: TranslationEntry, preferred_row: int = -1) -> int:
        row = self.find_entry_row(preferred_row, entry_label_key(entry))
        if row >= 0:
            self._translation_model.update_entry(row, entry)
            self._session = self._translation_model.session
        return row

    def _is_row_selected(self, row):
        return self.selectionModel().isRowSelected(row, QModelIndex())

    def _on_check_state_requested(self, row, checked):
        flag = QItemSelectionModel.SelectionFlag.Select if checked else QItemSelectionModel.SelectionFlag.Deselect
        self.selectionModel().select(self.model().index(row, COL_KEY), flag | QItemSelectionModel.SelectionFlag.Rows)

    def selectionCommand(self, index, event=None):  # noqa: N802 - Qt override
        if (
            index.column() == COL_CHECK
            and event is not None
            and event.type()
            in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
            )
        ):
            # The checkbox delegate toggles the selection through model.setData.
            # Selecting on mouse press first would make a fresh check toggle off.
            return QItemSelectionModel.SelectionFlag.NoUpdate
        return super().selectionCommand(index, event)

    def find_entry_row(self, preferred_row: int, entry_id: str) -> int:
        candidate = self.entry_at_row(preferred_row)
        if candidate is not None and entry_label_key(candidate) == entry_id:
            return preferred_row
        return self._entry_rows.get(entry_id, -1)

    def find_translation_index(self, preferred_row: int, entry_id: str) -> QModelIndex:
        return self.model().index(self.find_entry_row(preferred_row, entry_id), COL_TRANSLATION)

    def locate_entry(self, entry_id: str) -> None:
        row = self.find_entry_row(-1, entry_id)
        if row >= 0:
            self.focus_row(row, select=True)

    def focus_row(self, row, *, select=False):
        index = self.model().index(row, COL_KEY)
        if not index.isValid():
            return
        if select:
            self.selectRow(row)
        else:
            self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def selected_entry_ids(self) -> tuple[str, ...]:
        rows = self._translation_model.logical_rows
        if isinstance(rows, ResidentTableRows):
            return tuple(key for start, end in self.selected_row_spans() for key in rows.identities[start : end + 1])
        if self._translation_model.logical_count is not None:
            self._remember_logical_selection()
            return tuple(self._selected_logical_ids[row] for row in sorted(self._selected_logical_ids))
        return tuple(
            entry_label_key(entry)
            for index in self.selectionModel().selectedRows(COL_KEY)
            if (entry := self.entry_at_row(index.row())) is not None and entry.id
        )

    def _remember_logical_selection(self):
        spans = self.selected_row_spans()
        known = self._selected_logical_ids
        known.update((row, entry_label_key(entry)) for row, entry in self._translation_model.logical_rows.items())
        self._selected_logical_ids = {
            row: key for row, key in known.items() if any(start <= row <= end for start, end in spans)
        }

    def selected_row_spans(self):
        spans = []
        for start, end in sorted((span.top(), span.bottom()) for span in self.selectionModel().selection()):
            if spans and start <= spans[-1][1] + 1:
                spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
            else:
                spans.append((start, end))
        return tuple(spans)

    def viewport_rows(self):
        total = self.rowCount()
        if not total:
            return 0, -1
        first = self.rowAt(0)
        if first < 0:
            first = min(self.verticalScrollBar().value(), total - 1)
        last = self.rowAt(max(0, self.viewport().height() - 1))
        if last < 0:
            height = max(1, self.verticalHeader().defaultSectionSize())
            last = min(total - 1, first + self.viewport().height() // height)
        return first, max(first, last)

    def selectAll(self) -> None:  # noqa: N802 - Qt override
        super().selectAll()
        self.scope_select_all_requested.emit()

    def _capture_view_state(self):
        selected = self.selected_entry_ids()
        current = self.entry_at_row(self.currentIndex().row())
        scroll = self.verticalScrollBar().value()
        top_row = self.rowAt(0)
        if top_row < 0 and self.rowCount():
            top_row = min(scroll, self.rowCount() - 1)
        top = self.entry_at_row(top_row)
        return (
            selected,
            None if current is None else entry_label_key(current),
            (None if top is None or not top.id else entry_label_key(top)),
            scroll,
        )

    def _restore_view_state(self, selected, current, top, scroll):
        rows = sorted(self._entry_rows[key] for key in selected if key in self._entry_rows)
        if rows:
            selection = QItemSelection()
            start = previous = rows[0]
            for row in rows[1:]:
                if row != previous + 1:
                    selection.select(self.model().index(start, 0), self.model().index(previous, NUM_COLUMNS - 1))
                    start = row
                previous = row
            selection.select(self.model().index(start, 0), self.model().index(previous, NUM_COLUMNS - 1))
            self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.Select)
        if current in self._entry_rows:
            auto_scroll = self.hasAutoScroll()
            self.setAutoScroll(False)
            try:
                self.selectionModel().setCurrentIndex(
                    self.model().index(self._entry_rows[current], COL_KEY), QItemSelectionModel.SelectionFlag.NoUpdate
                )
            finally:
                self.setAutoScroll(auto_scroll)
        if top in self._entry_rows:
            self.scrollTo(
                self.model().index(self._entry_rows[top], COL_KEY), QAbstractItemView.ScrollHint.PositionAtTop
            )
        elif scroll:
            self.verticalScrollBar().setValue(min(scroll, self.verticalScrollBar().maximum()))


__all__ = ["TranslationTable"]
