"""Workbench filter controls and their local interaction state."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from transbridge.converter.translation_entry import STAGE_LABELS, TranslationEntry
from transbridge.ui.workbench.filters_presenter import ALL_CATEGORIES, FilterState, entry_category

TAG_NORMAL = (
    "QPushButton { background: #f0f0f0; border: 1px solid #ccc; border-radius: 8px; "
    "padding: 2px 10px; font-size: 12px; color: #333; }"
    "QPushButton:hover { background: #e0e0e0; }"
)
TAG_ACTIVE = (
    "QPushButton { background: #2196F3; border: 1px solid #1976D2; border-radius: 8px; "
    "padding: 2px 10px; font-size: 12px; color: white; font-weight: bold; }"
)


class FiltersView(QWidget):
    """Own filter widgets while exposing only filter intent to its facade."""

    def __init__(
        self,
        *,
        on_changed: Callable[[], None],
        on_manage_labels: Callable[[], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.category_filters: set[str] = set()
        self.stage_filters: set[int] = set()
        self.label_filters: set[str] = set()
        self.focus_labeled = False
        self._on_changed = on_changed
        self._on_manage_labels = on_manage_labels
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.addWidget(QLabel("筛选"))
        self.advanced_button = QPushButton("高级筛选 ▸")
        self.advanced_button.setAccessibleName("展开高级筛选")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setFlat(True)
        self.advanced_button.toggled.connect(self._set_advanced_visible)
        header.addWidget(self.advanced_button)
        header.addStretch()
        self.manage_labels_button = QPushButton("管理标签…")
        self.manage_labels_button.setAccessibleName("管理翻译标签")
        self.manage_labels_button.setFlat(True)
        self.manage_labels_button.clicked.connect(self._on_manage_labels)
        header.addWidget(self.manage_labels_button)
        outer.addLayout(header)

        self.category_widget, self.category_container = self._tag_row("分类：")
        outer.addWidget(self.category_widget)
        self.stage_widget, self.stage_container = self._tag_row("状态：")
        outer.addWidget(self.stage_widget)

        self.search_widget = QWidget()
        search_layout = QHBoxLayout(self.search_widget)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(6)
        self.search_key = self._search_field(search_layout, "Key:", "按 Key 筛选…")
        self.search_original = self._search_field(search_layout, "原文:", "按原文筛选…")
        self.search_translation = self._search_field(search_layout, "译文:", "按译文筛选…")
        self.clear_button = QPushButton("清除")
        self.clear_button.setAccessibleName("清除全部搜索条件")
        self.clear_button.setFixedWidth(52)
        self.clear_button.clicked.connect(self.clear_search)
        search_layout.addWidget(self.clear_button)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search_timer.timeout.connect(self._on_changed)
        for field in (self.search_key, self.search_original, self.search_translation):
            field.textChanged.connect(self.search_timer.start)
        outer.addWidget(self.search_widget)

        self.label_widget, self.label_container = self._tag_row("标签：")
        label_layout = self.label_widget.layout()
        self.focus_button = QPushButton("[已标记]")
        self.focus_button.setAccessibleName("只显示有标签的条目")
        self.focus_button.setCheckable(True)
        self.focus_button.setToolTip("只看有标签的条目")
        self.focus_button.setAccessibleDescription("未启用")
        self.focus_button.setStyleSheet(TAG_NORMAL)
        self.focus_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.focus_button.clicked.connect(self.toggle_focus)
        self.focus_button.setEnabled(False)
        label_layout.insertWidget(label_layout.count() - 1, self.focus_button)
        outer.addWidget(self.label_widget)
        self._advanced_visible = False
        self.set_content_visible(False)

    @staticmethod
    def _tag_row(title: str) -> tuple[QWidget, QHBoxLayout]:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel(title))
        container = QHBoxLayout()
        container.setSpacing(3)
        layout.addLayout(container)
        layout.addStretch()
        return widget, container

    @staticmethod
    def _search_field(layout: QHBoxLayout, title: str, placeholder: str) -> QLineEdit:
        layout.addWidget(QLabel(title))
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setAccessibleName(f"{title.rstrip(':：')}搜索")
        field.setClearButtonEnabled(True)
        layout.addWidget(field)
        return field

    def set_content_visible(self, visible: bool) -> None:
        self.stage_widget.setVisible(visible)
        self.search_widget.setVisible(visible)
        self.advanced_button.setEnabled(visible)
        self.category_widget.setVisible(visible and self._advanced_visible)
        if not visible:
            self.label_widget.hide()

    def _set_advanced_visible(self, visible: bool) -> None:
        self._advanced_visible = visible
        self.advanced_button.setText("高级筛选 ▾" if visible else "高级筛选 ▸")
        self.advanced_button.setAccessibleName("收起高级筛选" if visible else "展开高级筛选")
        self.category_widget.setVisible(visible and bool(self.category_container.count()))

    @staticmethod
    def _clear_layout(layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def build_categories(self, entries: Sequence[TranslationEntry]) -> None:
        self._clear_layout(self.category_container)
        if not entries:
            self.category_widget.hide()
            return
        counter = Counter(entry_category(entry) for entry in entries)
        self._add_tag(
            self.category_container,
            f"全部 {len(entries)}",
            not self.category_filters,
            lambda: self.toggle_category(None),
        )
        for category in ALL_CATEGORIES:
            count = counter.get(category, 0)
            if count:
                self._add_tag(
                    self.category_container,
                    f"{category} {count}",
                    category in self.category_filters,
                    lambda checked=False, value=category: self.toggle_category(value),
                )
        self.category_widget.setVisible(self._advanced_visible)

    def build_stages(self, entries: Sequence[TranslationEntry]) -> None:
        self._clear_layout(self.stage_container)
        if not entries:
            self.stage_widget.hide()
            return
        counter = Counter(entry.stage for entry in entries)
        self._add_tag(
            self.stage_container,
            f"全部 {len(entries)}",
            not self.stage_filters,
            lambda: self.toggle_stage(None),
        )
        for value, name in STAGE_LABELS.items():
            count = counter.get(value, 0)
            if count or value in self.stage_filters:
                self._add_tag(
                    self.stage_container,
                    f"{name} {count}",
                    value in self.stage_filters,
                    lambda checked=False, stage=value: self.toggle_stage(stage),
                )
        self.stage_widget.show()

    def build_labels(
        self,
        entries: Sequence[TranslationEntry],
        label_library: Mapping[str, Mapping[str, str]],
        entry_labels: Mapping[str, set[str]],
    ) -> None:
        self._clear_layout(self.label_container)
        if not label_library:
            self.label_widget.hide()
            self.focus_button.setEnabled(False)
            return
        counter = Counter(label for labels in entry_labels.values() for label in labels)
        labeled_count = sum(bool(labels) for labels in entry_labels.values())
        self._add_tag(
            self.label_container,
            f"全部 {labeled_count}",
            not self.label_filters,
            lambda: self.toggle_label(None),
        )
        for label_id, info in label_library.items():
            count = counter.get(label_id, 0)
            if count or label_id in self.label_filters:
                button = self._add_tag(
                    self.label_container,
                    f"● {info['name']} {count}",
                    label_id in self.label_filters,
                    lambda checked=False, value=label_id: self.toggle_label(value),
                )
                button.setStyleSheet(button.styleSheet() + f" color: {info['color']};")
        self.focus_button.setEnabled(bool(labeled_count))
        if not labeled_count and self.focus_labeled:
            self.focus_labeled = False
            self.focus_button.setChecked(False)
            self.focus_button.setStyleSheet(TAG_NORMAL)
        self.label_widget.setVisible(bool(entries))

    @staticmethod
    def _add_tag(
        layout: QHBoxLayout,
        text: str,
        active: bool,
        callback: Callable[..., None],
    ) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setChecked(active)
        button.setAccessibleName(f"筛选：{text}")
        button.setAccessibleDescription("已启用" if active else "未启用")
        button.setStyleSheet(TAG_ACTIVE if active else TAG_NORMAL)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def toggle_category(self, category: str | None) -> None:
        self._toggle(self.category_filters, category)
        self._on_changed()

    def toggle_stage(self, stage: int | None) -> None:
        self._toggle(self.stage_filters, stage)
        self._on_changed()

    def toggle_label(self, label_id: str | None) -> None:
        self._toggle(self.label_filters, label_id)
        self._on_changed()

    @staticmethod
    def _toggle(values: set, value) -> None:
        if value is None:
            values.clear()
        elif value in values:
            values.discard(value)
        else:
            values.add(value)

    def toggle_focus(self) -> None:
        self.focus_labeled = not self.focus_labeled
        self.focus_button.setChecked(self.focus_labeled)
        self.focus_button.setAccessibleDescription("已启用" if self.focus_labeled else "未启用")
        self.focus_button.setStyleSheet(TAG_ACTIVE if self.focus_labeled else TAG_NORMAL)
        self._on_changed()

    def clear_search(self) -> None:
        self.search_key.clear()
        self.search_original.clear()
        self.search_translation.clear()
        self.search_timer.stop()
        self._on_changed()

    def state(self) -> FilterState:
        return FilterState(
            categories=frozenset(self.category_filters),
            stages=frozenset(self.stage_filters),
            labels=frozenset(self.label_filters),
            search_key=self.search_key.text(),
            search_original=self.search_original.text(),
            search_translation=self.search_translation.text(),
            focus_labeled=self.focus_labeled,
        )

    def apply_state(self, state: FilterState) -> None:
        self.category_filters.clear()
        self.category_filters.update(state.categories)
        self.stage_filters.clear()
        self.stage_filters.update(state.stages)
        self.label_filters.clear()
        self.label_filters.update(state.labels)
        self.search_key.setText(state.search_key)
        self.search_original.setText(state.search_original)
        self.search_translation.setText(state.search_translation)
        self.search_timer.stop()
        self.focus_labeled = state.focus_labeled
        self.focus_button.setChecked(self.focus_labeled)
        self.focus_button.setAccessibleDescription("已启用" if self.focus_labeled else "未启用")
        self.focus_button.setStyleSheet(TAG_ACTIVE if self.focus_labeled else TAG_NORMAL)
