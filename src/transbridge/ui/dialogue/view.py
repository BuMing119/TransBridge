# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Task context browsing and a full-text translation editor."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from .models import DialogueTableModel, TopicTreeModel
from .quest_picker import QuestPicker
from .record_view import RecordFieldsView


def _label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class DialogueEditorView(QWidget):
    quest_selected = pyqtSignal(int, int)
    topic_selected = pyqtSignal(int)
    entry_selected = pyqtSignal(int)
    record_entry_selected = pyqtSignal(int)
    browse_mode_changed = pyqtSignal(str)
    draft_changed = pyqtSignal(str)
    apply_requested = pyqtSignal(bool)
    discard_requested = pyqtSignal()
    move_requested = pyqtSignal(int)
    close_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tbDialogueEditor")
        self.setAccessibleName("词条编辑器")
        self._entry_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        title = _label("词条编辑", self)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch()
        back = self._button("关闭", self.close_requested.emit)
        header.addWidget(back)
        layout.addLayout(header)
        self.source_label = _label("", self)
        layout.addWidget(self.source_label)
        self.context_reason = _label("", self)
        layout.addWidget(self.context_reason)
        self.message = _label("", self)
        self.message.hide()
        layout.addWidget(self.message)
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.context_panel = QWidget(self)
        context_layout = QVBoxLayout(self.context_panel)
        context_layout.setContentsMargins(0, 0, 8, 0)
        context_layout.addWidget(_label("任务记录 · DIAL / SCEN", self))
        self.quest_combo = QuestPicker(self)
        self.quest_combo.setAccessibleName("查找任务、话题或场景")
        self.quest_combo.setMinimumContentsLength(20)
        self.quest_combo.setSizeAdjustPolicy(QuestPicker.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        context_layout.addWidget(self.quest_combo)
        vertical = QSplitter(Qt.Orientation.Vertical, self)
        horizontal = QSplitter(Qt.Orientation.Horizontal, self)
        self.context_splitter = horizontal
        self.vertical_splitter = vertical
        self._context_available = True
        self._show_context_reason = False
        self._record_available = False
        self._context_sizes = [270, 850]
        self._vertical_sizes = [480, 230]
        self.tree = QTreeView(self)
        self.tree.setAccessibleName("当前任务的话题与场景记录")
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(180)
        self.tree_model = TopicTreeModel(self.tree)
        self.tree.setModel(self.tree_model)
        context_layout.addWidget(self.tree, 1)
        horizontal.addWidget(self.context_panel)
        self.table = QTableView(self)
        self.table.setAccessibleName("话题原文与译文")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table_model = DialogueTableModel(self.table)
        self.table.setModel(self.table_model)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        horizontal.addWidget(self.table)
        horizontal.setStretchFactor(1, 1)
        horizontal.setSizes([270, 850])
        self.browse_tabs = QTabWidget(self)
        self.browse_tabs.setAccessibleName("词条浏览方式")
        self.record_page = RecordFieldsView(self.browse_tabs)
        self.record_table = self.record_page.table
        self.record_model = self.record_page.model
        self.record_heading = self.record_page.heading
        self.browse_tabs.addTab(self.record_page, "ESP 记录")
        self.browse_tabs.addTab(horizontal, "话题与对话")
        self.browse_tabs.setTabVisible(0, False)
        self.browse_tabs.setCurrentIndex(1)
        vertical.addWidget(self.browse_tabs)
        self.editor_panel = QWidget(self)
        editor_layout = QVBoxLayout(self.editor_panel)
        editor_layout.setContentsMargins(0, 4, 0, 0)
        self.entry_label = _label("", self)
        self.entry_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(self.entry_label)
        fields = QSplitter(Qt.Orientation.Horizontal, self)
        self.original = QPlainTextEdit(self)
        self.original.setReadOnly(True)
        self.original.setAccessibleName("完整原文（只读）")
        self.original.setPlaceholderText("原文（只读）")
        self.translation = QPlainTextEdit(self)
        self.translation.setAccessibleName("译文草稿")
        self.original_label = _label("原文（只读）", self)
        self.translation_label = _label("译文草稿", self)
        for label, field in ((self.original_label, self.original), (self.translation_label, self.translation)):
            panel = QWidget(fields)
            field_layout = QVBoxLayout(panel)
            field_layout.setContentsMargins(0, 0, 0, 0)
            label.setBuddy(field)
            field_layout.addWidget(label)
            field_layout.addWidget(field, 1)
            fields.addWidget(panel)
        fields.setChildrenCollapsible(False)
        fields.setSizes([400, 600])
        editor_layout.addWidget(fields, 1)
        draft_status = QHBoxLayout()
        self.draft_label = _label("", self)
        draft_status.addWidget(self.draft_label, 1)
        self.navigation_label = _label("", self)
        self.navigation_label.setWordWrap(False)
        draft_status.addWidget(self.navigation_label)
        editor_layout.addLayout(draft_status)
        actions = QHBoxLayout()
        self.previous_button = self._button("上一条", lambda: self.move_requested.emit(-1))
        self.next_button = self._button("下一条", lambda: self.move_requested.emit(1))
        actions.addWidget(self.previous_button)
        actions.addWidget(self.next_button)
        actions.addStretch(1)
        self.discard_button = self._button("放弃草稿", self.discard_requested.emit)
        self.apply_button = self._button("应用并返回", lambda: self.apply_requested.emit(False))
        self.apply_button.setToolTip("应用当前译文并返回工作台")
        self.apply_next_button = self._button("应用并下一条", lambda: self.apply_requested.emit(True))
        ComponentStyle.apply_state(self.apply_next_button, SemanticState.PRIMARY)
        for button in (self.discard_button, self.apply_button, self.apply_next_button):
            actions.addWidget(button)
        editor_layout.addLayout(actions)
        vertical.addWidget(self.editor_panel)
        vertical.setSizes([480, 230])
        vertical.setChildrenCollapsible(False)
        horizontal.setChildrenCollapsible(False)
        body_layout.addWidget(vertical, 1)
        layout.addWidget(self.body, 1)
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        for field in (self.original, self.translation, self.quest_combo):
            ComponentStyle.apply_static(field, ComponentKind.INPUT)
        self.quest_combo.record_chosen.connect(self.quest_selected.emit)
        self.tree.selectionModel().currentChanged.connect(self._tree_selected)
        self.table.selectionModel().currentRowChanged.connect(
            lambda current, _old: self.entry_selected.emit(current.row())
        )
        self.record_page.entry_selected.connect(self.record_entry_selected.emit)
        self.browse_tabs.currentChanged.connect(self._browse_changed)
        self.translation.textChanged.connect(lambda: self.draft_changed.emit(self.translation.toPlainText()))
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.apply_next_button.click)
        self.body.setEnabled(False)

    def _button(self, text, callback):
        button = QPushButton(text, self)
        button.setAccessibleName(text)
        button.clicked.connect(callback)
        ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        return button

    def _tree_selected(self, current, _old) -> None:
        if current.isValid():
            self.topic_selected.emit(current.row())

    def show_quests(self, index, selected: int) -> None:
        self.quest_combo.set_dialogue_index(index, selected)

    def show_message(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def set_context_available(self, available: bool, reason: str = "", *, show_reason: bool = False) -> None:
        restore_sizes = available and not self._context_available
        if self._context_available and not available and self.context_splitter.isVisible():
            self._context_sizes = self.context_splitter.sizes()
            self._vertical_sizes = self.vertical_splitter.sizes()
        with QSignalBlocker(self.browse_tabs):
            self.browse_tabs.setTabVisible(1, available)
        self._context_available = available
        self._show_context_reason = show_reason
        self.context_panel.setEnabled(available)
        self.context_panel.setToolTip(reason)
        self.context_panel.setAccessibleDescription(reason)
        self.context_reason.setText(reason)
        self._refresh_browse_visibility()
        if restore_sizes:
            if self._context_sizes and any(self._context_sizes):
                self.context_splitter.setSizes(self._context_sizes)
            if self._vertical_sizes and any(self._vertical_sizes):
                self.vertical_splitter.setSizes(self._vertical_sizes)
        if not available:
            self.quest_combo.set_dialogue_index(None)
            with QSignalBlocker(self.tree.selectionModel()):
                self.tree_model.set_quest(None)

    @property
    def browsing_mode(self) -> str:
        return "record" if self.browse_tabs.currentIndex() == 0 else "dialogue"

    def set_record_available(self, available: bool) -> None:
        self._record_available = available
        with QSignalBlocker(self.browse_tabs):
            self.browse_tabs.setTabVisible(0, available)
        self._refresh_browse_visibility()

    def set_browse_mode(self, mode: str) -> None:
        index = 0 if mode == "record" else 1
        if self.browse_tabs.isTabVisible(index):
            with QSignalBlocker(self.browse_tabs):
                self.browse_tabs.setCurrentIndex(index)
        self._refresh_browse_visibility()

    def _refresh_browse_visibility(self) -> None:
        self.browse_tabs.setVisible(self._context_available or self._record_available)
        dialogue = self.browsing_mode == "dialogue" and self._context_available
        self.context_reason.setVisible(
            bool(self.context_reason.text()) and (self._show_context_reason or not self._record_available or dialogue)
        )

    def _browse_changed(self, _index: int) -> None:
        self._refresh_browse_visibility()
        self.browse_mode_changed.emit(self.browsing_mode)

    def show_record(self, entries, row: int, *, record_type="", editor_id="", form_id="") -> None:
        self.record_page.show_record(entries, row, record_type=record_type, editor_id=editor_id, form_id=form_id)

    def show_quest(self, quest, topic_row: int) -> None:
        with QSignalBlocker(self.tree.selectionModel()):
            self.tree_model.set_quest(quest)
            current = self.tree_model.index(topic_row, 0)
            self.tree.setCurrentIndex(current)
            self.tree.scrollTo(current)

    def show_entries(self, entries, row: int) -> None:
        with QSignalBlocker(self.table.selectionModel()):
            self.table_model.set_entries(entries)
            self.table.selectRow(row)
            self.table.scrollTo(self.table_model.index(row, 0))

    def show_entry(self, entry, text: str) -> None:
        self.editor_panel.setEnabled(entry is not None)
        self.entry_label.setText("" if entry is None else entry.key)
        self.original.setPlainText("" if entry is None else entry.original)
        with QSignalBlocker(self.translation):
            key = None if entry is None else entry.identity
            # An index result may arrive while typing. Preserve cursor and undo
            # for the same draft, but never carry undo history into another entry.
            if self._entry_key != key or self.translation.toPlainText() != text:
                self.translation.setPlainText(text)
            self._entry_key = key

    def show_draft_state(self, changed: bool, count: int) -> None:
        self.draft_label.setText(f"未应用草稿 {count} 条" if count else "译文已应用到工程")
        self.apply_button.setEnabled(changed)
        self.discard_button.setEnabled(changed)

    def show_navigation(self, position: int, count: int, has_context: bool, *, scope: str | None = None) -> None:
        scope = scope or ("任务／场景" if has_context else "打开时列表")
        self.navigation_label.setText(f"{scope} · {position + 1} / {count}")
        last = position + 1 == count
        text = "应用并返回" if last else "应用并下一条"
        self.apply_next_button.setText(text)
        self.apply_next_button.setAccessibleName(text)
        self.apply_next_button.setToolTip("已到最后一条，应用后返回工作台" if last else "应用后继续编辑下一条")
