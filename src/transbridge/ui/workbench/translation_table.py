"""Lazy Workbench table view with stable translation identity and selection."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.project_labels import entry_label_key

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
        self._entry_rows: dict[str, int] = {}
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
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(30)
        self.setMouseTracking(True)

    def set_editable_entry_keys(self, keys) -> None:
        self._editable_entry_keys = frozenset(keys)

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
        self._closed = False
        self._session = session
        self._entry_labels, self._label_library = entry_labels, label_library
        self._display_order = self._sorting.order(session.entries, entry_labels)
        self._entry_rows = {
            entry_label_key(session.entries[source_row]): row for row, source_row in enumerate(self._display_order)
        }
        reset = self._translation_model.install(session, self._display_order, entry_labels, label_library)
        if reset:
            self._restore_view_state(
                selected, current, top if preserve_scroll else None, scroll if preserve_scroll else 0
            )
        total = len(session.entries)
        self._on_progress(total, total)
        self._on_batch()

    def replace_session(self, session: RenderSession) -> None:
        """Install replacement entries while retaining all existing Qt indexes."""
        self._translation_model.replace_session(session)
        self._session = session

    def _sort_display(self) -> None:
        self.start_render(self._session, self._entry_labels, self._label_library, preserve_scroll=False)
        self.scrollToTop()

    def close_rendering(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._entry_labels, self._label_library = {}, {}
        self._display_order = ()
        self._entry_rows.clear()
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
            self.selectRow(row)
            self.scrollTo(self.model().index(row, COL_KEY), QAbstractItemView.ScrollHint.PositionAtCenter)

    def selected_entry_ids(self) -> tuple[str, ...]:
        return tuple(
            entry_label_key(entry)
            for index in self.selectionModel().selectedRows(COL_KEY)
            if (entry := self.entry_at_row(index.row())) is not None and entry.id
        )

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
