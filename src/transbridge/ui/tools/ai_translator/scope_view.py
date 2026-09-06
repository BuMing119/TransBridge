"""Rendering helpers for the AI translator scope controls."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from transbridge.converter.translation_entry import STAGE_LABELS
from transbridge.ui.foundation.components import ComponentDensity, ComponentKind, ComponentStyle, SemanticState
from transbridge.ui.tools.ai_translator.scope_presenter import ScopeEstimate, ScopePresenter, TranslationScope
from transbridge.ui.tools.ai_translator.view_controls import TranslatorViewOwner
from transbridge.ui.tools.ai_translator.view_state import ScopeOptions

from .task_widget_style import configure_task_input, configure_task_panel


class ScopeCallbacks(Protocol):
    def on_preset(self, preset: str) -> None: ...
    def on_scope_stage_clicked(self, stage: int | None) -> None: ...
    def on_scope_label_clicked(self, label_id: str | None) -> None: ...
    def on_scope_category_clicked(self, category: str | None) -> None: ...


def render_scope_tags(
    view: TranslatorViewOwner,
    callbacks: ScopeCallbacks,
    *,
    entries: list,
    state: TranslationScope,
    label_library: dict[str, dict],
    entry_labels: dict[str, set[str]],
    categories: tuple[str, ...],
    category_of: Callable[[object], str],
) -> None:
    for button, selected in (
        (view.controls.scope_stage_all_btn, state.stage_filters),
        (view.controls.scope_label_all_btn, state.label_filters),
        (view.controls.scope_cat_all_btn, state.category_filters),
    ):
        button.setText(f"已选 {len(selected)} 项" if selected else "不限")
    view.controls.preset_selection.setText(f"当前选择 {len(state.selected_entry_ids)}")
    _style_filter(view.controls.preset_selection, state.preset == "selection")
    _style_filter(view.controls.preset_untranslated, state.stage_filters == frozenset({0}) and state.preset is None)
    _style_filter(view.controls.preset_table_view, state.preset == "table_view")

    stage_counts = Counter(entry.stage for entry in entries)
    for stage, label in STAGE_LABELS.items():
        button = view.controls.scope_stage_btns.get(stage)
        if button is None:
            button = _new_button(view.controls.scope_stage_all_btn, lambda s=stage: callbacks.on_scope_stage_clicked(s))
            view.controls.scope_stage_btns[stage] = button
        button.setText(f"{label} {stage_counts.get(stage, 0)}")
        _style_filter(button, stage in state.stage_filters)

    label_counts = Counter(label for labels in entry_labels.values() for label in labels)
    for label_id, info in label_library.items():
        button = view.controls.scope_label_btns.get(label_id)
        if button is None:
            button = _new_button(
                view.controls.scope_label_all_btn,
                lambda value=label_id: callbacks.on_scope_label_clicked(value),
            )
            view.controls.scope_label_btns[label_id] = button
        button.setText(f"● {info.get('name', '?')} {label_counts.get(label_id, 0)}")
        _style_filter(button, label_id in state.label_filters)

    category_counts = Counter(category_of(entry) for entry in entries)
    for category in categories:
        button = view.controls.scope_cat_btns.get(category)
        if button is None:
            button = _new_button(
                view.controls.scope_cat_all_btn,
                lambda value=category: callbacks.on_scope_category_clicked(value),
            )
            view.controls.scope_cat_btns[category] = button
        button.setText(f"{category} {category_counts.get(category, 0)}")
        _style_filter(button, category in state.category_filters)


def _style_filter(button: QPushButton, active: bool) -> None:
    button.setCheckable(True)
    button.setChecked(active)
    button.setAccessibleName(f"AI 范围筛选：{button.text()}")
    button.setAccessibleDescription("已启用" if active else "未启用")
    ComponentStyle.apply_static(button, ComponentKind.BADGE, ComponentDensity.COMPACT)
    ComponentStyle.apply_state(button, SemanticState.CHECKED if active else SemanticState.DEFAULT)


def _new_button(anchor: QPushButton, callback: Callable[[], None]) -> QPushButton:
    menu = anchor.menu()
    button = QPushButton(menu)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda _checked=False: callback())
    action = QWidgetAction(menu)
    action.setDefaultWidget(button)
    menu.addAction(action)
    return button


def _filter_menu(anchor: QPushButton, reset: Callable[[], None]) -> None:
    menu = QMenu(anchor)
    menu.addAction("不限", reset)
    anchor.setMenu(menu)
    anchor.setMinimumWidth(140)


def render_scope_estimate(view: TranslatorViewOwner, estimate: ScopeEstimate) -> None:
    label = view.controls.mixed_estimate_lbl if estimate.target == "mixed" else view.controls.estimate_lbl
    label.setText(estimate.text)


def refresh_scope_estimate(view: TranslatorViewOwner, presenter: ScopePresenter, options: ScopeOptions) -> None:
    estimate = presenter.estimate(
        mode=options.mode,
        rules=options.rules,
        overwrite=options.overwrite,
        max_tokens=options.max_tokens,
        model=view.controls.model_edit.text().strip(),
        max_concurrent=options.max_concurrent,
    )
    render_scope_estimate(view, estimate)


def build_scope_view(view: TranslatorViewOwner, callbacks: ScopeCallbacks) -> None:
    # ── 翻译范围区 ────────────────────────────────────────────────────────
    scope_box = QGroupBox("翻译范围")
    configure_task_panel(scope_box)
    scope_layout = QVBoxLayout(scope_box)
    scope_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    scope_layout.setSpacing(4)

    # 快捷预设按钮
    preset_row = QHBoxLayout()
    preset_row.addWidget(QLabel("快捷："))
    view.controls.preset_untranslated = QPushButton("全部未翻译")
    view.controls.preset_untranslated.setCursor(Qt.CursorShape.PointingHandCursor)
    view.controls.preset_untranslated.clicked.connect(lambda: callbacks.on_preset("untranslated"))
    preset_row.addWidget(view.controls.preset_untranslated)
    view.controls.preset_selection = QPushButton("当前选择 0")
    view.controls.preset_selection.setCursor(Qt.CursorShape.PointingHandCursor)
    view.controls.preset_selection.clicked.connect(lambda: callbacks.on_preset("selection"))
    preset_row.addWidget(view.controls.preset_selection)
    view.controls.preset_table_view = QPushButton("当前主表视图")
    view.controls.preset_table_view.setCursor(Qt.CursorShape.PointingHandCursor)
    view.controls.preset_table_view.clicked.connect(lambda: callbacks.on_preset("table_view"))
    preset_row.addWidget(view.controls.preset_table_view)
    preset_row.addStretch()
    scope_layout.addLayout(preset_row)

    # 翻译状态维度标签
    stage_row = QHBoxLayout()
    stage_row.setSpacing(3)
    stage_row.addWidget(QLabel("状态："))
    view.controls.scope_stage_all_btn = QPushButton("不限")
    view.controls.scope_stage_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    _filter_menu(view.controls.scope_stage_all_btn, lambda: callbacks.on_scope_stage_clicked(None))
    stage_row.addWidget(view.controls.scope_stage_all_btn)
    view.controls.scope_stage_btns: dict[int, QPushButton] = {}
    stage_row.addStretch()
    scope_layout.addLayout(stage_row)

    # 标记维度标签
    mark_row = QHBoxLayout()
    mark_row.setSpacing(3)
    mark_row.addWidget(QLabel("标记："))
    view.controls.scope_label_all_btn = QPushButton("不限")
    view.controls.scope_label_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    _filter_menu(view.controls.scope_label_all_btn, lambda: callbacks.on_scope_label_clicked(None))
    mark_row.addWidget(view.controls.scope_label_all_btn)
    view.controls.scope_label_btns: dict[str, QPushButton] = {}
    mark_row.addStretch()
    scope_layout.addLayout(mark_row)

    # 分类维度标签
    cat_row = QHBoxLayout()
    cat_row.setSpacing(3)
    cat_row.addWidget(QLabel("分类："))
    view.controls.scope_cat_all_btn = QPushButton("不限")
    view.controls.scope_cat_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    _filter_menu(view.controls.scope_cat_all_btn, lambda: callbacks.on_scope_category_clicked(None))
    cat_row.addWidget(view.controls.scope_cat_all_btn)
    view.controls.scope_cat_btns: dict[str, QPushButton] = {}
    cat_row.addStretch()
    scope_layout.addLayout(cat_row)

    view.controls.overwrite_check = QCheckBox("覆盖已有译文（重新翻译）")
    scope_layout.addWidget(view.controls.overwrite_check)
    hint = QLabel("筛选条件应用于已选插件；当前选择和主表视图仅针对打开任务时的当前内容。")
    hint.setWordWrap(True)
    hint.setProperty("tbTaskHint", True)
    scope_layout.addWidget(hint)

    view.controls.estimate_lbl = QLabel("预计：— 条")
    _configure_estimate_label(view.controls.estimate_lbl)
    view.controls.estimate_lbl.setAccessibleName("AI 翻译范围估算")
    scope_layout.addWidget(view.controls.estimate_lbl)

    # ── 混合模式面板 ──────────────────────────────────────────────────────
    mixed_panel = QWidget()
    mixed_layout = QVBoxLayout(mixed_panel)
    mixed_layout.setContentsMargins(0, 0, 0, 0)
    from ._rule_editor_widget import _RuleEditorWidget

    view.controls.rule_editor = _RuleEditorWidget()
    mixed_layout.addWidget(view.controls.rule_editor)
    order_row = QHBoxLayout()
    order_row.addWidget(QLabel("执行顺序:"))
    view.controls.order_combo = QComboBox()
    configure_task_input(view.controls.order_combo)
    view.controls.order_combo.addItems(["串行（先翻译后润色）", "并行"])
    order_row.addWidget(view.controls.order_combo)
    order_row.addStretch()
    mixed_layout.addLayout(order_row)
    view.controls.mixed_estimate_lbl = view.controls.estimate_lbl

    view.controls.scope_stack = QStackedWidget()
    view._scope_filter_box = scope_box
    view.controls.scope_stack.addWidget(QWidget())
    view.controls.scope_stack.addWidget(mixed_panel)
    view.controls.scope_stack.setVisible(False)


def _configure_estimate_label(label: QLabel) -> None:
    """Keep long run estimates readable inside the horizontal-scroll-free viewport."""

    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
