"""Context-menu view for Workbench translation entries."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QMenu, QWidget

from transbridge.converter.translation_entry import STAGE_LABELS, TranslationEntry


def build_entry_menu(
    entry: TranslationEntry,
    *,
    label_library: Mapping[str, Mapping[str, str]],
    assigned_labels: set[str],
    on_label_toggle: Callable[[str, str, bool], None],
    on_manage_labels: Callable[[], None],
    on_create_label: Callable[[TranslationEntry], None],
    on_stage_change: Callable[[TranslationEntry, int], None],
    parent: QWidget,
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
            action.toggled.connect(
                lambda checked, entry_id=entry.id, value=label_id: on_label_toggle(entry_id, value, checked)
            )
    label_menu.addSeparator()
    label_menu.addAction("管理标签…", on_manage_labels)
    label_menu.addAction("+ 新建标签…", lambda: on_create_label(entry))

    stage_menu = menu.addMenu("翻译状态")
    stage_group = QActionGroup(stage_menu)
    stage_group.setExclusive(True)
    for stage_value, stage_name in sorted(STAGE_LABELS.items()):
        action = stage_menu.addAction(stage_name)
        action.setCheckable(True)
        action.setChecked(stage_value == entry.stage)
        stage_group.addAction(action)
        action.toggled.connect(lambda checked, value=stage_value: on_stage_change(entry, value) if checked else None)
    return menu
