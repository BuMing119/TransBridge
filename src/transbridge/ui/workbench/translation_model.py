"""Lazy cell data over the Workbench's single translation render snapshot."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal

from transbridge.converter.translation_entry import STAGE_LABELS
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.source_input_state import original_known

from .filters_presenter import entry_category
from .resident_rows import ResidentTableRows
from .table_presenter import RenderSession
from .translation_cell_text import full_text_tooltip, translation_cell_text
from .translation_table_columns import (
    COL_CHECK,
    COL_INDEX,
    COL_KEY,
    COL_MARK,
    COL_ORIGINAL,
    COL_TRANSLATION,
    NUM_COLUMNS,
)

_HEADERS = ("", "#", "标签", "Key", "原文", "译文", "类型 / 状态")


class TranslationTableModel(QAbstractTableModel):
    """Expose values on demand; edits remain intents until the command succeeds."""

    translation_edit_requested = pyqtSignal(object, str, int)
    check_state_requested = pyqtSignal(int, bool)

    def __init__(self, parent=None, *, is_row_selected=None):
        super().__init__(parent)
        self.session = RenderSession(0, None, ())
        self.order = ()
        self.entry_labels = {}
        self.label_library = {}
        self.projection = {}
        self.profile_label = None
        self.pending = False
        self.error = ""
        self._is_row_selected = is_row_selected or (lambda _row: False)
        self._header_tooltips = {}
        self._generation = 1
        self._editing_allowed = True
        self.default_ranks = {}
        self._source_keys = ()
        self.logical_count = None
        self.logical_rows = {}
        self._logical_overrides = {}

    def install(self, session, order, entry_labels, label_library, *, source_keys=None) -> bool:
        """Install data and report whether existing Qt indexes were invalidated."""
        if source_keys is None:
            source_keys = tuple(entry.identity for entry in session.entries)
        same_rows = source_keys == self._source_keys and order == self.order
        if not same_rows:
            current_keys = tuple(self._source_keys[row] for row in self.order)
            incoming_keys = tuple(source_keys[row] for row in order)
            same_rows = bool(current_keys) and current_keys == incoming_keys
        same_scope = (
            self.logical_count is None
            and session.content_scope is not None
            and session.content_scope == self.session.content_scope
        )
        same_editability = self._editing_allowed == (self.profile_label is None)
        self._editing_allowed = self.profile_label is None
        self._source_keys = source_keys
        if same_scope and same_editability and self.order and same_rows:
            # Legacy callers can mutate entries in place. Always notify readers;
            # equality against the old session would miss these mutations.
            self.session, self.order = session, order
            self.entry_labels, self.label_library = entry_labels, label_library
            self.dataChanged.emit(self.index(0, 0), self.index(len(order) - 1, NUM_COLUMNS - 1), [])
            return False
        self.beginResetModel()
        self._generation += 1
        self.logical_count, self.logical_rows = None, {}
        self._logical_overrides.clear()
        self.session, self.order = session, order
        self.entry_labels, self.label_library = entry_labels, label_library
        self.endResetModel()
        return True

    def install_logical(self, session, total, rows, entry_labels, label_library, *, editing_row=None):
        """Update cached cells without invalidating indexes within the same logical range."""
        reset = self.logical_count != total or self.session.content_scope != session.content_scope
        before = self.logical_rows
        pinned = None if editing_row is None or reset else self.entry_at_row(editing_row)
        if reset:
            self.beginResetModel()
            self._generation += 1
        self.logical_count, self.logical_rows = total, rows
        self._logical_overrides = {} if pinned is None else {editing_row: pinned}
        self.session, self.order, self._source_keys = session, (), ()
        self.entry_labels, self.label_library = entry_labels, label_library
        self._editing_allowed = self.profile_label is None
        if reset:
            self.endResetModel()
        elif isinstance(before, ResidentTableRows) or isinstance(rows, ResidentTableRows):
            for first, last in (
                ((0, total - 1),)
                if editing_row is None
                else (
                    (0, editing_row - 1),
                    (editing_row + 1, total - 1),
                )
            ):
                if first <= last:
                    self.dataChanged.emit(self.index(first, 0), self.index(last, NUM_COLUMNS - 1), [])
        else:
            changed = sorted((before.keys() | rows.keys()) - {editing_row})
            if changed:
                first = previous = changed[0]
                for row in changed[1:]:
                    if row != previous + 1:
                        self.dataChanged.emit(self.index(first, 0), self.index(previous, NUM_COLUMNS - 1), [])
                        first = row
                    previous = row
                self.dataChanged.emit(self.index(first, 0), self.index(previous, NUM_COLUMNS - 1), [])
        return reset

    def replace_session(self, session):
        """Replace equal-sized source data without invalidating persistent indexes."""
        if len(session.entries) != len(self.session.entries) or any(
            before.identity != after.identity
            for before, after in zip(self.session.entries, session.entries, strict=True)
        ):
            raise ValueError("Replacing a table session requires unchanged row membership")
        self.session = session

    def set_terminology_projection(self, values, *, profile_label, pending=False, error=""):
        self.projection, self.profile_label = values, profile_label
        self.pending, self.error = pending, error

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 - Qt override
        return 0 if parent.isValid() else self.logical_count if self.logical_count is not None else len(self.order)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802 - Qt override
        return 0 if parent.isValid() else NUM_COLUMNS

    def index(self, row, column, parent=QModelIndex()):
        if parent.isValid() or not 0 <= row < self.rowCount() or not 0 <= column < NUM_COLUMNS:
            return QModelIndex()
        return self.createIndex(row, column, self._generation)

    def _owns_index(self, index):
        return index.isValid() and index.model() is self and index.internalId() == self._generation

    def entry_at_row(self, row):
        if self.logical_count is not None:
            return self._logical_overrides.get(row) or self.logical_rows.get(row)
        return self.session.entries[self.order[row]] if 0 <= row < len(self.order) else None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802 - Qt override
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < NUM_COLUMNS:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._header_tooltips.get(section, "")
        return None

    def setHeaderData(self, section, orientation, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.ToolTipRole:
            return False
        if not 0 <= section < NUM_COLUMNS:
            return False
        self._header_tooltips[section] = str(value)
        self.headerDataChanged.emit(orientation, section, section)
        return True

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not self._owns_index(index) or not 0 <= index.column() < NUM_COLUMNS:
            return None
        column = index.column()
        if self.logical_count is not None and column == COL_INDEX and role == Qt.ItemDataRole.DisplayRole:
            return str(index.row() + 1)
        entry = self.entry_at_row(index.row())
        if entry is None:
            if self.logical_count is not None and column == COL_ORIGINAL and role == Qt.ItemDataRole.DisplayRole:
                return "正在加载…"
            return None
        if role == Qt.ItemDataRole.UserRole:
            return entry
        if role == Qt.ItemDataRole.CheckStateRole and column == COL_CHECK:
            return Qt.CheckState.Checked if self._is_row_selected(index.row()) else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (COL_CHECK, COL_INDEX, COL_MARK):
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.EditRole and column == COL_TRANSLATION:
            return entry.translation or ""
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == COL_MARK:
                labels = self._labels(entry)
                return "\n".join(self.label_library[label]["name"] for label in labels if label in self.label_library)
            if column == COL_KEY:
                return full_text_tooltip(entry.key or "")
            if column == COL_ORIGINAL:
                if not original_known(entry):
                    return "此条目仅保存了译文；需要原文的操作可选择文件补充输入。"
                return full_text_tooltip(entry.original or "")
            if column == COL_TRANSLATION:
                return self._translation_content(entry)[1]
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == COL_CHECK:
            return ""
        if column == COL_INDEX:
            return str(self.default_ranks.get(entry.identity, self.order[index.row()]) + 1)
        if column == COL_MARK:
            count = len(self._labels(entry))
            return str(count) if count else ""
        if column == COL_KEY:
            return entry.key or ""
        if column == COL_ORIGINAL:
            return (entry.original or "") if original_known(entry) else "原文待补充"
        if column == COL_TRANSLATION:
            return self._translation_content(entry)[0]
        projected = self._projected_translation(entry)
        derived = (
            " · 术语待确认"
            if tuple(getattr(projected, "diagnostics", ()))
            else " · 已应用译名方案"
            if bool(getattr(projected, "is_derived", False))
            else ""
        )
        return f"{entry_category(entry)} · {STAGE_LABELS.get(entry.stage, f'状态 {entry.stage}')}{derived}"

    def flags(self, index):
        if not self._owns_index(index):
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.entry_at_row(index.row()) is None:
            return flags if self.logical_count is not None else Qt.ItemFlag.NoItemFlags
        if index.column() == COL_CHECK:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        elif index.column() == COL_TRANSLATION and self.profile_label is None:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802 - Qt override
        if not self._owns_index(index):
            return False
        entry = self.entry_at_row(index.row())
        if entry is None:
            return False
        if index.column() == COL_CHECK and role == Qt.ItemDataRole.CheckStateRole:
            self.check_state_requested.emit(index.row(), value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value))
            return True
        if index.column() == COL_TRANSLATION and role == Qt.ItemDataRole.EditRole and self.profile_label is None:
            self.translation_edit_requested.emit(entry, str(value), index.row())
            return True
        return False

    def update_entry(self, row, entry):
        if self.logical_count is not None:
            if self.entry_at_row(row) is not None:
                self._logical_overrides[row] = entry
                self.dataChanged.emit(self.index(row, 0), self.index(row, NUM_COLUMNS - 1), [])
            return
        if not 0 <= row < len(self.order):
            return
        source_row = self.order[row]
        if self.session.entries[source_row] is not entry:
            entries = list(self.session.entries)
            entries[source_row] = entry
            self.session = replace(self.session, entries=tuple(entries))
        self.dataChanged.emit(self.index(row, 0), self.index(row, NUM_COLUMNS - 1), [])

    def selection_changed(self, selected, deselected):
        for selection in (selected, deselected):
            for selected_range in selection:
                first, last = selected_range.top(), min(selected_range.bottom(), self.rowCount() - 1)
                if 0 <= first <= last:
                    self.dataChanged.emit(
                        self.index(first, COL_CHECK), self.index(last, COL_CHECK), [Qt.ItemDataRole.CheckStateRole]
                    )

    def _labels(self, entry):
        return self.entry_labels.get(entry_label_key(entry), ()) if entry.id else ()

    def _projected_translation(self, entry):
        return self.projection.get(entry.identity.serialize()) if self.projection else None

    def _translation_content(self, entry):
        return translation_cell_text(
            entry,
            self._projected_translation(entry),
            profile_label=self.profile_label,
            pending=self.pending,
            error=self.error,
        )
