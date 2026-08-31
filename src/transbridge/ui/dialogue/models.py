"""Virtual Qt views over the task/topic projection and current topic entries."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractItemModel, QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QFont

from transbridge.application.dialogue.index import DialogueQuest, record_type
from transbridge.converter.translation_entry import STAGE_LABELS, TranslationEntry


class TopicTreeModel(QAbstractItemModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.quest: DialogueQuest | None = None

    def set_quest(self, quest: DialogueQuest | None) -> None:
        self.beginResetModel()
        self.quest = quest
        self.endResetModel()

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index=QModelIndex()):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.quest.topics) if self.quest is not None and not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if self.quest is None or not index.isValid():
            return None
        topic = self.quest.topics[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return topic.label
        if role == Qt.ItemDataRole.ToolTipRole:
            return topic.tooltip or topic.label
        if role == Qt.ItemDataRole.UserRole:
            return topic.identity
        return None


class DialogueTableModel(QAbstractTableModel):
    HEADERS = ("记录 / 字段", "原文", "译文", "状态")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.entries: tuple[TranslationEntry, ...] = ()

    def set_entries(self, entries: tuple[TranslationEntry, ...]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self.entries[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return entry.identity
        if role == Qt.ItemDataRole.FontRole and record_type(entry) == "DIAL":
            font = QFont()
            font.setBold(True)
            return font
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if index.column() == 0:
                return entry.key if role == Qt.ItemDataRole.ToolTipRole else (entry.context or "").split("|", 1)[0]
            if index.column() == 1:
                return entry.original
            if index.column() == 2:
                return entry.translation
            return STAGE_LABELS.get(entry.stage, str(entry.stage))
        return None
