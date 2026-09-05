"""Plugin selection surface for a batch AI translation task."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

_ROLE_HAS_UNTRANSLATED = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_TOTAL = _ROLE_HAS_UNTRANSLATED + 1
_ROLE_UNTRANSLATED = _ROLE_TOTAL + 1


class BatchPluginList(QWidget):
    """Keep selection, ordering and statistics independent from dialog composition."""

    selection_changed = pyqtSignal()

    def __init__(self, ctx: object, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setMinimumWidth(250)
        self.setMaximumWidth(340)
        ComponentStyle.apply_static(self, ComponentKind.CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.header = QLabel("翻译内容", self)
        self.header.setProperty("tbTaskSectionTitle", True)
        header_font = self.header.font()
        header_font.setBold(True)
        self.header.setFont(header_font)
        layout.addWidget(self.header)

        hint = QLabel("勾选插件；可拖拽调整处理顺序", self)
        hint.setProperty("tbTaskHint", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QListWidget(self)
        self.list.setProperty("tbTaskList", True)
        self.list.setAccessibleName("批量翻译插件列表")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSpacing(3)
        layout.addWidget(self.list, 1)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.select_all_button = QPushButton("全选", self)
        self.clear_button = QPushButton("清除", self)
        ComponentStyle.apply_static(self.select_all_button, ComponentKind.BUTTON)
        ComponentStyle.apply_static(self.clear_button, ComponentKind.BUTTON)
        controls.addWidget(self.select_all_button)
        controls.addWidget(self.clear_button)
        layout.addLayout(controls)
        self.untranslated_button = QPushButton("仅选有未翻译内容", self)
        ComponentStyle.apply_static(self.untranslated_button, ComponentKind.BUTTON)
        layout.addWidget(self.untranslated_button)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        self.summary = QLabel(self)
        self.summary.setProperty("tbTaskMeta", True)
        self.summary.setAccessibleName("批量翻译插件选择统计")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.select_all_button.clicked.connect(self.select_all)
        self.clear_button.clicked.connect(self.clear_selection)
        self.untranslated_button.clicked.connect(self.select_untranslated)
        self.list.itemChanged.connect(self._notify)
        self.list.model().rowsMoved.connect(self._notify)
        self.populate()

    def populate(self) -> None:
        self.list.clear()
        slots = getattr(self._ctx, "slots", {})
        for key, slot in slots.items():
            collection = getattr(slot, "collection", None)
            total = len(collection) if collection else 0
            untranslated = sum(1 for entry in collection or () if not entry.translation or entry.stage == 0)
            name = getattr(slot, "label", None) or Path(str(key)).stem
            detail = "已完成" if total and untranslated == 0 else f"未翻 {untranslated}"
            item = QListWidgetItem(f"{name}\n{total} 条 · {detail}")
            item.setToolTip(f"{name} — 共 {total} 条，未翻译 {untranslated} 条")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if untranslated > 0 else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setData(_ROLE_HAS_UNTRANSLATED, untranslated > 0)
            item.setData(_ROLE_TOTAL, total)
            item.setData(_ROLE_UNTRANSLATED, untranslated)
            row_height = max(52, self.list.fontMetrics().height() * 2 + 16)
            item.setSizeHint(QSize(0, row_height))
            self.list.addItem(item)
        self._notify()

    def select_all(self) -> None:
        self._set_checks(lambda _item: True)

    def clear_selection(self) -> None:
        self._set_checks(lambda _item: False)

    def select_untranslated(self) -> None:
        self._set_checks(lambda item: bool(item.data(_ROLE_HAS_UNTRANSLATED)))

    def selected_slots(self) -> list[object]:
        slots = getattr(self._ctx, "slots", {})
        selected: list[object] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                slot = slots.get(item.data(Qt.ItemDataRole.UserRole))
                if slot is not None:
                    selected.append(slot)
        return selected

    def counts(self, *, overwrite: bool = False) -> tuple[int, int, int]:
        plugins = entries = untranslated = 0
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            plugins += 1
            entries += int(item.data(_ROLE_TOTAL) or 0)
            untranslated += int(item.data(_ROLE_UNTRANSLATED) or 0)
        return plugins, entries if overwrite else untranslated, untranslated

    def _set_checks(self, predicate) -> None:
        previous = self.list.blockSignals(True)
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setCheckState(Qt.CheckState.Checked if predicate(item) else Qt.CheckState.Unchecked)
        self.list.blockSignals(previous)
        self._notify()

    def _notify(self, *_args) -> None:
        selected, _effective, untranslated = self.counts()
        self.header.setText(f"翻译内容（{selected}/{self.list.count()}）")
        self.summary.setText(f"已选 {selected} 个插件 · 未翻译 {untranslated} 条")
        self.summary.setAccessibleDescription(self.summary.text())
        self.selection_changed.emit()


__all__ = ["BatchPluginList"]
