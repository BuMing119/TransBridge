"""Task context browsing and a full-text translation editor."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .models import DialogueTableModel, TopicTreeModel


def _label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class DialogueEditorView(QWidget):
    quest_selected = pyqtSignal(int)
    topic_selected = pyqtSignal(int)
    entry_selected = pyqtSignal(int)
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
        self.message = _label("请选择包含任务上下文的插件内容。", self)
        layout.addWidget(self.message)
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.context_panel = QWidget(self)
        context_layout = QVBoxLayout(self.context_panel)
        context_layout.setContentsMargins(0, 0, 8, 0)
        context_layout.addWidget(_label("任务记录 · DIAL / SCEN", self))
        self.quest_combo = QComboBox(self)
        self.quest_combo.setAccessibleName("选择任务")
        self.quest_combo.setMinimumContentsLength(20)
        self.quest_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        context_layout.addWidget(self.quest_combo)
        vertical = QSplitter(Qt.Orientation.Vertical, self)
        horizontal = QSplitter(Qt.Orientation.Horizontal, self)
        self.tree = QTreeView(self)
        self.tree.setAccessibleName("当前任务的话题与场景记录")
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(180)
        self.tree_model = TopicTreeModel(self.tree)
        self.tree.setModel(self.tree_model)
        context_layout.addWidget(self.tree, 1)
        self.context_reason = _label("", self)
        context_layout.addWidget(self.context_reason)
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
        vertical.addWidget(horizontal)
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
        self.translation.setPlaceholderText("输入译文；应用后写入当前工程")
        fields.addWidget(self.original)
        fields.addWidget(self.translation)
        fields.setSizes([400, 600])
        editor_layout.addWidget(fields, 1)
        actions = QHBoxLayout()
        self.previous_button = self._button("上一条", lambda: self.move_requested.emit(-1))
        self.next_button = self._button("下一条", lambda: self.move_requested.emit(1))
        actions.addWidget(self.previous_button)
        actions.addWidget(self.next_button)
        self.draft_label = _label("", self)
        actions.addWidget(self.draft_label, 1)
        self.discard_button = self._button("放弃草稿", self.discard_requested.emit)
        self.apply_button = self._button("应用译文", lambda: self.apply_requested.emit(False))
        self.apply_next_button = self._button("应用并下一条", lambda: self.apply_requested.emit(True))
        for button in (self.discard_button, self.apply_button, self.apply_next_button):
            actions.addWidget(button)
        editor_layout.addLayout(actions)
        vertical.addWidget(self.editor_panel)
        vertical.setSizes([480, 230])
        vertical.setChildrenCollapsible(False)
        horizontal.setChildrenCollapsible(False)
        body_layout.addWidget(vertical, 1)
        layout.addWidget(self.body, 1)
        layout.addWidget(
            _label(
                "左侧按 FormID 浏览记录；SCEN 显示引用话题，不表示条件分支或实际演出顺序。Ctrl+Enter 应用并继续。", self
            )
        )
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        for field in (self.original, self.translation, self.quest_combo):
            ComponentStyle.apply_static(field, ComponentKind.INPUT)
        self.quest_combo.currentIndexChanged.connect(self.quest_selected.emit)
        self.tree.selectionModel().currentChanged.connect(self._tree_selected)
        self.table.selectionModel().currentRowChanged.connect(
            lambda current, _old: self.entry_selected.emit(current.row())
        )
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
        with QSignalBlocker(self.quest_combo):
            self.quest_combo.clear()
            for quest in index.quests:
                self.quest_combo.addItem(quest.label)
            self.quest_combo.setCurrentIndex(selected)

    def set_context_available(self, available: bool, reason: str = "") -> None:
        self.context_panel.setEnabled(available)
        self.context_panel.setToolTip(reason)
        self.context_panel.setAccessibleDescription(reason)
        self.context_reason.setText(reason)
        self.context_reason.setVisible(bool(reason))
        if not available:
            with QSignalBlocker(self.quest_combo), QSignalBlocker(self.tree.selectionModel()):
                self.quest_combo.clear()
                self.tree_model.set_quest(None)

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
