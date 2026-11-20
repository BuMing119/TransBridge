# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Search quests, topics and scenes without changing the active editor selection."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QHideEvent, QKeyEvent, QWheelEvent
from PyQt6.QtWidgets import QAbstractItemView, QFrame, QLineEdit, QListView, QVBoxLayout, QWidget

from transbridge.application.dialogue.index import DialogueIndex
from transbridge.ui.foundation.components.elided_combo_box import ElidedComboBox


@dataclass(frozen=True, slots=True)
class _Record:
    identity: tuple[str, ...]
    label: str
    name: str
    form_id: str
    quest: int
    topic: int
    is_quest: bool

    def matches(self, query: str) -> bool:
        return query in self.name or query in self.form_id

    def exact(self, query: str) -> bool:
        return query == self.name or query == self.form_id


class _ResultsModel(QAbstractListModel):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.records: list[_Record] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt override
        return 0 if parent.isValid() else len(self.records)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.records):
            return None
        record = self.records[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return record.label
        if role == Qt.ItemDataRole.UserRole:
            return record.quest, record.topic
        return None

    def replace(self, records: list[_Record]) -> None:
        self.beginResetModel()
        self.records = records
        self.endResetModel()


class _SearchPopup(QFrame):
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAccessibleName("搜索任务、话题或场景")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("搜索任务、话题或场景（名称 / FormID）")
        self.search_input.setAccessibleName(self.search_input.placeholderText())
        self.search_input.setClearButtonEnabled(True)
        self.results = QListView(self)
        self.results.setAccessibleName("任务、话题与场景搜索结果")
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setUniformItemSizes(True)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.search_input)
        layout.addWidget(self.results, 1)
        self.setFocusProxy(self.search_input)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self.dismissed.emit()


class QuestPicker(ElidedComboBox):
    """Keep native quest labels while committing search results only on explicit choice."""

    record_chosen = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index: DialogueIndex | None = None
        self._records: list[_Record] = []
        self.popup = _SearchPopup(self)
        self.search_input = self.popup.search_input
        self.results = self.popup.results
        self._results_model = _ResultsModel(self.results)
        self.results.setModel(self._results_model)
        for widget in (self.popup, self.search_input, self.results):
            widget.installEventFilter(self)
        self.search_input.textChanged.connect(self._filter_results)
        self.results.clicked.connect(self._choose_index)
        self.popup.dismissed.connect(self._reset_search)
        self.activated.connect(self._choose_closed_quest)

    def set_dialogue_index(self, index: DialogueIndex | None, selected: int = -1) -> None:
        """Refresh navigation without disturbing an open search or emitting a choice."""
        current = self._current_record()
        identity = current.identity if current else None
        changed = index is not self._index
        with QSignalBlocker(self):
            previous = self.currentData()
            if changed:
                self.clear()
                if index is not None:
                    for quest in index.quests:
                        self.addItem(quest.label, quest.identity)
            if index is not None:
                if not 0 <= selected < self.count():
                    selected = next((row for row, quest in enumerate(index.quests) if quest.identity == previous), -1)
                self.setCurrentIndex(selected)
        self._index = index
        if changed:
            self._records = self._build_records(index)
        if index is None or not index.quests:
            self.hidePopup()
            self._reset_search()
        elif changed and self.popup.isVisible():
            self._filter_results(identity=identity)

    @staticmethod
    def _build_records(index: DialogueIndex | None) -> list[_Record]:
        records = []
        if index is None:
            return records
        for quest_row, quest in enumerate(index.quests):
            labels = [(quest.identity, quest.label, quest.identity[-1], 0, True)]
            labels.extend(
                (topic.identity, topic.label, topic.identity[-1].removeprefix("SCEN:"), row, False)
                for row, topic in enumerate(quest.topics)
                if topic.kind in {"DIAL", "SCEN"} and topic.identity[-1] != "journal"
            )
            for identity, label, form_id, topic_row, is_quest in labels:
                name = label.partition(" {")[2].rpartition("}")[0]
                records.append(
                    _Record(
                        identity,
                        label if is_quest else f"{label} — {quest.label}",
                        name.casefold(),
                        form_id.casefold(),
                        quest_row,
                        topic_row,
                        is_quest,
                    )
                )
        return records

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        if not self.isEnabled() or self._index is None or not self._index.quests:
            return
        if self.popup.isVisible():
            self.search_input.setFocus(Qt.FocusReason.PopupFocusReason)
            return
        self._filter_results()
        screen = self.screen().availableGeometry()
        width = min(max(self.width(), 440), screen.width())
        height = min(340, screen.height())
        position = self.mapToGlobal(QPoint(0, self.height()))
        if position.y() + height > screen.bottom() + 1:
            position.setY(self.mapToGlobal(QPoint(0, 0)).y() - height)
        position.setX(max(screen.left(), min(position.x(), screen.right() - width + 1)))
        position.setY(max(screen.top(), position.y()))
        self.popup.resize(width, height)
        self.popup.move(position)
        self.popup.show()
        self.search_input.setFocus(Qt.FocusReason.PopupFocusReason)

    def hidePopup(self) -> None:  # noqa: N802 - Qt override
        self.popup.hide()
        super().hidePopup()

    def _reset_search(self) -> None:
        with QSignalBlocker(self.search_input):
            self.search_input.clear()
        if self._results_model.records:
            self._results_model.replace([])

    def _current_record(self) -> _Record | None:
        row = self.results.currentIndex().row()
        return self._results_model.records[row] if 0 <= row < len(self._results_model.records) else None

    def _filter_results(self, _text: str = "", *, identity: tuple[str, ...] | None = None) -> None:
        query = self.search_input.text().strip().casefold()
        if query:
            records = [record for record in self._records if record.matches(query)]
            records.sort(key=lambda record: not record.exact(query))
        else:
            records = [record for record in self._records if record.is_quest]
        self._results_model.replace(records)
        row = next((row for row, record in enumerate(records) if record.identity == identity), -1)
        if row < 0 and not query:
            row = next((row for row, record in enumerate(records) if record.quest == self.currentIndex()), -1)
        if row < 0 and records:
            row = 0
        self.results.setCurrentIndex(self._results_model.index(row, 0))

    def _choose_index(self, index: QModelIndex) -> None:
        if not index.isValid() or not 0 <= index.row() < len(self._results_model.records):
            return
        record = self._results_model.records[index.row()]
        self.hidePopup()
        with QSignalBlocker(self):
            self.setCurrentIndex(record.quest)
        self.record_chosen.emit(record.quest, record.topic)

    def _choose_closed_quest(self, row: int) -> None:
        if not self.popup.isVisible() and 0 <= row < self.count():
            self.record_chosen.emit(row, 0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hidePopup()
                self.setFocus(Qt.FocusReason.PopupFocusReason)
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._choose_index(self.results.currentIndex())
            elif watched is self.search_input and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.results.currentIndex().row()
                step = 1 if key == Qt.Key.Key_Down else -1
                row = max(0, min(row + step, self._results_model.rowCount() - 1))
                self.results.setCurrentIndex(self._results_model.index(row, 0))
            else:
                return super().eventFilter(watched, event)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_F4):
            self.showPopup()
            event.accept()
        elif (
            event.text()
            and event.text().isprintable()
            and not event.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            self.showPopup()
            if self.popup.isVisible():
                self.search_input.insert(event.text())
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        event.ignore()
