"""Editable raw mappings with incremental dirty tracking and deferred validation."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal

from transbridge.application.terminology_profiles import ProfileTermMapping

_HEADERS = ("原文术语", "当前译文中的叫法", "方案译名", "生效范围", "插件文件")
SCOPE_LABELS = {"project": "整个工程", "plugin": "仅指定插件"}


def _raw_mapping(mapping: ProfileTermMapping | None) -> tuple[str, ...]:
    if mapping is None:
        return ("", "", "", "project", "")
    return (
        mapping.original,
        mapping.base_translation,
        mapping.translation,
        mapping.scope_kind,
        mapping.plugin_id or "",
    )


class ProfileMappingModel(QAbstractTableModel):
    """Keep incomplete input without constructing domain mappings on every edit."""

    dirty_changed = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, ...]] = []
        self._baseline: tuple[tuple[str, ...], ...] = ()
        self._changed_rows: set[int] = set()

    @property
    def is_dirty(self) -> bool:
        return len(self._rows) != len(self._baseline) or bool(self._changed_rows)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal and 0 <= section < len(_HEADERS):
                return _HEADERS[section]
            if orientation == Qt.Orientation.Vertical:
                return str(section + 1)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        value = self._rows[index.row()][index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return SCOPE_LABELS[value] if index.column() == 3 else value
        if role == Qt.ItemDataRole.EditRole:
            return value
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.column() != 4 or self._rows[index.row()][3] == "plugin":
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not isinstance(value, str):
            return False
        if not index.isValid() or not self.flags(index) & Qt.ItemFlag.ItemIsEditable:
            return False
        row, column = index.row(), index.column()
        if column == 3 and value not in SCOPE_LABELS:
            return False
        values = list(self._rows[row])
        values[column] = value
        if column == 3 and value == "project":
            values[4] = ""
        replacement = tuple(values)
        if replacement == self._rows[row]:
            return True
        was_dirty = self.is_dirty
        self._rows[row] = replacement
        self._track_row(row)
        last = self.index(row, 4) if column == 3 else index
        self.dataChanged.emit(index, last, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        self._notify_dirty(was_dirty)
        return True

    def load(self, mappings) -> None:
        baseline = tuple(_raw_mapping(mapping) for mapping in mappings)
        was_dirty = self.is_dirty
        self.beginResetModel()
        self._baseline = baseline
        self._rows = list(baseline)
        self._changed_rows.clear()
        self.endResetModel()
        self._notify_dirty(was_dirty)

    def append_mapping(self, mapping: ProfileTermMapping | None = None) -> None:
        was_dirty = self.is_dirty
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(_raw_mapping(mapping))
        self._track_row(row)
        self.endInsertRows()
        self._notify_dirty(was_dirty)

    def removeRows(self, row, count, parent=QModelIndex()) -> bool:
        if parent.isValid() or count <= 0 or row < 0 or row + count > len(self._rows):
            return False
        was_dirty = self.is_dirty
        self.beginRemoveRows(QModelIndex(), row, row + count - 1)
        del self._rows[row : row + count]
        self._changed_rows = {index for index in self._changed_rows if index < row}
        for index in range(row, len(self._rows)):
            self._track_row(index)
        self.endRemoveRows()
        self._notify_dirty(was_dirty)
        return True

    def read_mappings(self) -> tuple[ProfileTermMapping, ...]:
        return tuple(
            ProfileTermMapping(
                original=source.strip(),
                translation=target.strip(),
                base_translation=base.strip(),
                scope_kind=scope,
                plugin_id=plugin.strip() if scope == "plugin" else None,
            )
            for source, base, target, scope, plugin in self._rows
        )

    def _track_row(self, row: int) -> None:
        if row >= len(self._baseline) or self._rows[row] != self._baseline[row]:
            self._changed_rows.add(row)
        else:
            self._changed_rows.discard(row)

    def _notify_dirty(self, was_dirty: bool) -> None:
        if self.is_dirty != was_dirty:
            self.dirty_changed.emit(self.is_dirty)


__all__ = ["ProfileMappingModel", "SCOPE_LABELS"]
