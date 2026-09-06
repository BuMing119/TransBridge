"""Select ordered content sources for any AI task mode."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .task_widget_style import configure_task_button, configure_task_list, configure_task_title

_ROLE_NAME = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_TOTAL = _ROLE_NAME + 1
_ROLE_COUNT = _ROLE_TOTAL + 1


class TaskSourcesView(QWidget):
    """Render candidates supplied by the shared task scope; never estimate them here."""

    selection_changed = pyqtSignal()

    def __init__(self, ctx: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots = dict(getattr(ctx, "slots", {}) or {})
        self.setMinimumWidth(230)
        self.setMaximumWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        self.header = QLabel("处理内容", self)
        configure_task_title(self.header, "section")
        layout.addWidget(self.header)
        hint = QLabel("勾选一个或多个插件；拖拽调整顺序", self)
        hint.setWordWrap(True)
        configure_task_title(hint, "hint")
        layout.addWidget(hint)
        self.list = QListWidget(self)
        configure_task_list(self.list)
        self.list.setAccessibleName("AI 任务处理内容")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setMinimumHeight(100)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.select_all_button = QPushButton("全选", self)
        self.clear_button = QPushButton("清除", self)
        for button in (self.select_all_button, self.clear_button):
            configure_task_button(button)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.pending_button = QPushButton("仅选有待处理内容", self)
        configure_task_button(self.pending_button)
        self.pending_button.setEnabled(False)
        layout.addWidget(self.pending_button)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("AI 任务内容选择统计")
        configure_task_title(self.summary, "meta")
        layout.addWidget(self.summary)

        active = getattr(ctx, "active_slot", None)
        active_key = next((key for key, slot in self._slots.items() if slot is active), None)
        default_key = active_key if active_key is not None else next(iter(self._slots), None)
        for key, slot in self._slots.items():
            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setData(_ROLE_NAME, getattr(slot, "label", None) or Path(str(key)).stem)
            collection = getattr(slot, "collection", None)
            item.setData(_ROLE_TOTAL, len(collection) if collection is not None else 0)
            item.setCheckState(Qt.CheckState.Checked if key == default_key else Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(0, max(54, self.list.fontMetrics().height() * 2 + 18)))
            self._render_item(item)
            self.list.addItem(item)
        self._render_summary()
        self.select_all_button.clicked.connect(lambda: self._set_checks(lambda _item: True))
        self.clear_button.clicked.connect(lambda: self._set_checks(lambda _item: False))
        self.pending_button.clicked.connect(lambda: self._set_checks(lambda item: (item.data(_ROLE_COUNT) or 0) > 0))
        self.list.itemChanged.connect(self._notify)
        self.list.model().rowsMoved.connect(self._notify)

    def selected_slots(self) -> list[object]:
        return [self._slots[item.data(Qt.ItemDataRole.UserRole)] for item in self._items() if self._checked(item)]

    def set_counts(self, counts: dict[str, int]) -> None:
        """Apply current-mode candidate counts without changing selection or emitting selection signals."""
        previous = self.list.blockSignals(True)
        try:
            for item in self._items():
                count = counts.get(item.data(Qt.ItemDataRole.UserRole))
                item.setData(_ROLE_COUNT, count)
                self._render_item(item)
        finally:
            self.list.blockSignals(previous)
        self.pending_button.setEnabled(
            bool(self._slots) and all(item.data(_ROLE_COUNT) is not None for item in self._items())
        )
        self._render_summary()

    def _items(self) -> list[QListWidgetItem]:
        return [self.list.item(index) for index in range(self.list.count())]

    @staticmethod
    def _checked(item: QListWidgetItem) -> bool:
        return item.checkState() == Qt.CheckState.Checked

    @staticmethod
    def _render_item(item: QListWidgetItem) -> None:
        count = item.data(_ROLE_COUNT)
        pending = "待估算" if count is None else f"本次 {count} 条"
        text = f"{item.data(_ROLE_NAME)}\n共 {item.data(_ROLE_TOTAL)} 条 · {pending}"
        item.setText(text)
        item.setToolTip(text)

    def _set_checks(self, predicate: Callable[[QListWidgetItem], bool]) -> None:
        previous = self.list.blockSignals(True)
        try:
            for item in self._items():
                item.setCheckState(Qt.CheckState.Checked if predicate(item) else Qt.CheckState.Unchecked)
        finally:
            self.list.blockSignals(previous)
        self._notify()

    def _render_summary(self) -> None:
        selected = [item for item in self._items() if self._checked(item)]
        self.header.setText(f"处理内容（{len(selected)}/{self.list.count()}）")
        counts = [item.data(_ROLE_COUNT) for item in selected]
        detail = "待估算" if any(count is None for count in counts) else f"本次 {sum(counts)} 条"
        self.summary.setText(f"已选 {len(selected)} 个插件 · {detail}")
        self.summary.setAccessibleDescription(self.summary.text())

    def _notify(self, *_args: object) -> None:
        self._render_summary()
        self.selection_changed.emit()


__all__ = ["TaskSourcesView"]
