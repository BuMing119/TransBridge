"""Workbench filter controls and their local interaction state."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from transbridge.converter.translation_entry import STAGE_LABELS, TranslationEntry
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentDensity, ComponentKind, ComponentStyle, SemanticState
from transbridge.ui.workbench.filters_presenter import ALL_CATEGORIES, FilterState, entry_category

from ._theme_support import readable_user_color


class FiltersView(QWidget):
    """Own filter widgets while exposing only filter intent to its facade."""

    def __init__(
        self,
        *,
        on_changed: Callable[[], None],
        on_manage_labels: Callable[[], None],
        parent=None,
        theme_view: ThemeView | None = None,
    ) -> None:
        super().__init__(parent)
        self.category_filters: set[str] = set()
        self.stage_filters: set[int] = set()
        self.label_filters: set[str] = set()
        self.focus_labeled = False
        self._on_changed = on_changed
        self._on_manage_labels = on_manage_labels
        self._theme_view = theme_view
        self._label_buttons: list[QPushButton] = []
        self._build()
        if theme_view is not None:
            theme_view.subscribe(self, self._apply_theme)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.search_widget = QWidget(self)
        header = QHBoxLayout(self.search_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.filter_button = QPushButton("筛选 ▾")
        self.filter_button.setAccessibleName("展开常用状态筛选")
        self.filter_button.setCheckable(True)
        ComponentStyle.apply_static(self.filter_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        self.filter_button.toggled.connect(self._set_common_filters_visible)
        header.addWidget(self.filter_button)
        self.advanced_button = QPushButton("高级筛选 ▸")
        self.advanced_button.setAccessibleName("展开高级筛选")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setFlat(True)
        ComponentStyle.apply_static(self.advanced_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        self.advanced_button.toggled.connect(self._set_advanced_visible)
        header.addWidget(self.advanced_button)
        self.search_all = QLineEdit(self)
        self.search_all.setPlaceholderText("搜索 Key / 原文 / 译文…")
        self.search_all.setAccessibleName("搜索全部词条字段")
        self.search_all.setClearButtonEnabled(True)
        ComponentStyle.apply_static(self.search_all, ComponentKind.INPUT, ComponentDensity.COMPACT)
        header.addWidget(self.search_all, 1)
        self.clear_button = QPushButton("清除")
        self.clear_button.setAccessibleName("清除全部搜索条件")
        ComponentStyle.apply_static(self.clear_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        self.clear_button.clicked.connect(self.clear_search)
        header.addWidget(self.clear_button)
        self.manage_labels_button = QPushButton("管理标签…")
        self.manage_labels_button.setAccessibleName("管理翻译标签")
        self.manage_labels_button.setFlat(True)
        ComponentStyle.apply_static(self.manage_labels_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        self.manage_labels_button.clicked.connect(self._on_manage_labels)
        header.addWidget(self.manage_labels_button)
        outer.addWidget(self.search_widget)

        self.category_widget, self.category_container = self._tag_row("分类：")
        outer.addWidget(self.category_widget)
        self.stage_widget, self.stage_container = self._tag_row("状态：")
        outer.addWidget(self.stage_widget)

        self.advanced_search_widget = QWidget()
        search_layout = QHBoxLayout(self.advanced_search_widget)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(6)
        self.search_key = self._search_field(search_layout, "Key:", "按 Key 筛选…")
        self.search_original = self._search_field(search_layout, "原文:", "按原文筛选…")
        self.search_translation = self._search_field(search_layout, "译文:", "按译文筛选…")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search_timer.timeout.connect(self._on_changed)
        for field in (self.search_all, self.search_key, self.search_original, self.search_translation):
            field.textChanged.connect(self.search_timer.start)
        outer.addWidget(self.advanced_search_widget)

        self.label_widget, self.label_container = self._tag_row("标签：")
        label_layout = self.label_widget.layout()
        self.focus_button = QPushButton("[已标记]")
        self.focus_button.setAccessibleName("只显示有标签的条目")
        self.focus_button.setCheckable(True)
        self.focus_button.setToolTip("只看有标签的条目")
        self.focus_button.setAccessibleDescription("未启用")
        self._style_tag(self.focus_button, False)
        self.focus_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.focus_button.clicked.connect(self.toggle_focus)
        self.focus_button.setEnabled(False)
        label_layout.insertWidget(label_layout.count() - 1, self.focus_button)
        outer.addWidget(self.label_widget)
        self._advanced_visible = False
        self._content_visible = False
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
        ComponentStyle.apply_static(field, ComponentKind.INPUT, ComponentDensity.COMPACT)
        layout.addWidget(field)
        return field

    def set_content_visible(self, visible: bool) -> None:
        self._content_visible = visible
        self.search_widget.setVisible(visible)
        self.advanced_button.setEnabled(visible)
        self.filter_button.setEnabled(visible)
        self.stage_widget.setVisible(visible and self.filter_button.isChecked())
        self.category_widget.setVisible(visible and self._advanced_visible)
        self.advanced_search_widget.setVisible(visible and self._advanced_visible)
        if not visible:
            self.label_widget.hide()

    def _set_advanced_visible(self, visible: bool) -> None:
        self._advanced_visible = visible
        self.advanced_button.setText("高级筛选 ▾" if visible else "高级筛选 ▸")
        self.advanced_button.setAccessibleName("收起高级筛选" if visible else "展开高级筛选")
        self.category_widget.setVisible(self._content_visible and visible and bool(self.category_container.count()))
        self.advanced_search_widget.setVisible(self._content_visible and visible)
        self.label_widget.setVisible(self._content_visible and visible and self.focus_button.isEnabled())

    def _set_common_filters_visible(self, visible: bool) -> None:
        self.filter_button.setText("筛选 ▴" if visible else "筛选 ▾")
        self.filter_button.setAccessibleName("收起常用状态筛选" if visible else "展开常用状态筛选")
        self.stage_widget.setVisible(self._content_visible and visible)

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
        self.stage_widget.setVisible(self._content_visible and self.filter_button.isChecked())

    def build_labels(
        self,
        entries: Sequence[TranslationEntry],
        label_library: Mapping[str, Mapping[str, str]],
        entry_labels: Mapping[str, set[str]],
    ) -> None:
        self._clear_layout(self.label_container)
        self._label_buttons.clear()
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
                button.setProperty("tbUserColor", info.get("color"))
                self._label_buttons.append(button)
                self._apply_user_color(button)
        self.focus_button.setEnabled(bool(labeled_count))
        if not labeled_count and self.focus_labeled:
            self.focus_labeled = False
            self.focus_button.setChecked(False)
            self.sync_focus_style()
        self.label_widget.setVisible(bool(entries) and self._advanced_visible)

    def _add_tag(
        self,
        layout: QHBoxLayout,
        text: str,
        active: bool,
        callback: Callable[..., None],
    ) -> QPushButton:
        button = QPushButton()
        visible_text = button.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, 172)
        button.setText(visible_text)
        button.setMaximumWidth(196)
        button.setToolTip(text)
        button.setCheckable(True)
        button.setChecked(active)
        button.setAccessibleName(f"筛选：{text}")
        button.setAccessibleDescription("已启用" if active else "未启用")
        self._style_tag(button, active)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    @staticmethod
    def _style_tag(button: QPushButton, active: bool) -> None:
        ComponentStyle.apply_static(button, ComponentKind.BADGE, ComponentDensity.COMPACT)
        ComponentStyle.apply_state(button, SemanticState.CHECKED if active else SemanticState.DEFAULT)
        button.setAccessibleDescription("已启用" if active else "未启用")

    def _apply_user_color(self, button: QPushButton) -> None:
        palette = button.palette()
        palette.setColor(
            QPalette.ColorRole.ButtonText,
            readable_user_color(
                button.property("tbUserColor"),
                palette,
                background_role=QPalette.ColorRole.Button,
                foreground_role=QPalette.ColorRole.ButtonText,
            ),
        )
        button.setPalette(palette)

    def _apply_theme(self, _snapshot) -> None:
        for button in self._label_buttons:
            self._apply_user_color(button)

    def sync_focus_style(self) -> None:
        self.focus_button.setChecked(self.focus_labeled)
        self._style_tag(self.focus_button, self.focus_labeled)

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
        self.sync_focus_style()
        self._on_changed()

    def clear_search(self) -> None:
        self.search_all.clear()
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
            search_all=self.search_all.text(),
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
        self.search_all.setText(state.search_all)
        self.search_key.setText(state.search_key)
        self.search_original.setText(state.search_original)
        self.search_translation.setText(state.search_translation)
        self.search_timer.stop()
        self.focus_labeled = state.focus_labeled
        self.sync_focus_style()
