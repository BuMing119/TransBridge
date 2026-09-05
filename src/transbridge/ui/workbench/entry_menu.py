"""Context-menu view for Workbench translation entries."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QMenu, QWidget

from transbridge.converter.translation_entry import STAGE_LABELS


def build_entry_menu(
    *,
    target_entry_ids: tuple[str, ...],
    current_stage: int | None,
    label_library: Mapping[str, Mapping[str, str]],
    assigned_labels: set[str],
    on_label_toggle: Callable[[tuple[str, ...], str, bool], None],
    on_manage_labels: Callable[[], None],
    on_create_label: Callable[[tuple[str, ...]], None],
    on_stage_change: Callable[[int], None],
    parent: QWidget,
    on_cancel_translation: Callable[[], None] | None = None,
    cancel_translation_enabled: bool = False,
) -> QMenu:
    """Build a stateless menu that emits intents through explicit callbacks."""
    menu = QMenu(parent)
    label_menu = menu.addMenu("标签")
    if not label_library:
        no_label = label_menu.addAction("暂无标签，请先创建")
        no_label.setEnabled(False)
    else:
        for label_id, info in label_library.items():
            action = label_menu.addAction(f"● {info['name']}")
            action.setCheckable(True)
            action.setChecked(label_id in assigned_labels)
            action.toggled.connect(lambda checked, value=label_id: on_label_toggle(target_entry_ids, value, checked))
    label_menu.addSeparator()
    label_menu.addAction("管理标签…", on_manage_labels)
    label_menu.addAction("+ 新建标签…", lambda: on_create_label(target_entry_ids))

    stage_menu = menu.addMenu("翻译状态")
    stage_group = QActionGroup(stage_menu)
    stage_group.setExclusive(True)
    for stage_value, stage_name in sorted(STAGE_LABELS.items()):
        action = stage_menu.addAction(stage_name)
        action.setCheckable(True)
        action.setChecked(stage_value == current_stage)
        stage_group.addAction(action)
        action.triggered.connect(lambda _checked=False, value=stage_value: on_stage_change(value))
    menu.addSeparator()
    cancel_action = menu.addAction("取消翻译")
    cancel_action.setToolTip("清空译文并恢复为“未翻译”；右键选中行时应用于全部选中词条。")
    cancel_action.setEnabled(cancel_translation_enabled and on_cancel_translation is not None)
    if on_cancel_translation is not None:
        cancel_action.triggered.connect(on_cancel_translation)
    return menu
