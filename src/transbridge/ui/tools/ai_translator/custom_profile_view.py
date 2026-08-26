"""Small widget slice for named custom AI workflow profiles."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfileDocument
from transbridge.ui.tools.ai_translator.view_controls import TranslatorViewOwner


def build_custom_profile_view(view: TranslatorViewOwner) -> QGroupBox:
    """Create the custom-profile selector without owning persistence behavior."""

    group = QGroupBox("自定义工作流")
    layout = QVBoxLayout(group)
    selector_row = QHBoxLayout()
    selector_row.addWidget(QLabel("配置:"))
    view.controls.custom_profile_combo = QComboBox()
    view.controls.custom_profile_combo.setMinimumContentsLength(16)
    view.controls.custom_profile_combo.setAccessibleName("自定义工作流配置")
    selector_row.addWidget(view.controls.custom_profile_combo, 1)
    for name, text in (
        ("custom_profile_new_btn", "新建"),
        ("custom_profile_rename_btn", "重命名"),
        ("custom_profile_delete_btn", "删除"),
        ("custom_profile_import_btn", "导入…"),
        ("custom_profile_export_btn", "导出…"),
    ):
        button = QPushButton(text)
        setattr(view.controls, name, button)
        selector_row.addWidget(button)
    layout.addLayout(selector_row)

    base_row = QHBoxLayout()
    base_row.addWidget(QLabel("基础入口:"))
    view.controls.custom_base_mode_combo = QComboBox()
    view.controls.custom_base_mode_combo.addItem("翻译", "translate")
    view.controls.custom_base_mode_combo.addItem("润色", "polish")
    view.controls.custom_base_mode_combo.addItem("混合", "mixed")
    view.controls.custom_base_mode_combo.setToolTip("复用所选入口的作用域、运行器、报告和提交规则")
    base_row.addWidget(view.controls.custom_base_mode_combo)
    view.controls.custom_profile_status_label = QLabel("请新建或导入配置")
    view.controls.custom_profile_status_label.setAccessibleName("自定义工作流状态")
    base_row.addWidget(view.controls.custom_profile_status_label, 1)
    layout.addLayout(base_row)
    group.setVisible(False)
    return group


class CustomProfileWidgetView:
    """Render profile aggregate state without owning persistence or dialogs."""

    def __init__(self, view: TranslatorViewOwner) -> None:
        self._view = view

    def render_profiles(self, document: CustomWorkflowProfileDocument) -> None:
        controls = self._view.controls
        combo = controls.custom_profile_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            for profile in document.profiles:
                combo.addItem(profile.name, profile.id)
            if document.selected_profile_id is not None:
                selected_index = combo.findData(document.selected_profile_id)
                combo.setCurrentIndex(selected_index)
        finally:
            combo.blockSignals(False)
        profile = document.selected_profile
        available = profile is not None
        for button in (
            controls.custom_profile_rename_btn,
            controls.custom_profile_delete_btn,
            controls.custom_profile_export_btn,
        ):
            button.setEnabled(available)
        if profile is None:
            controls.custom_profile_status_label.setText("请新建或导入配置；没有配置时无法启动")
            return
        base_index = controls.custom_base_mode_combo.findData(profile.base_mode)
        controls.custom_base_mode_combo.blockSignals(True)
        try:
            controls.custom_base_mode_combo.setCurrentIndex(base_index)
        finally:
            controls.custom_base_mode_combo.blockSignals(False)
        strategy = "一次校对润色" if profile.strategy == "combined" else "严格多阶段"
        controls.custom_profile_status_label.setText(f"当前：{profile.name} · {strategy}")

    def render_profile_error(self, message: str) -> None:
        self._view.controls.custom_profile_status_label.setText(f"自定义配置加载失败：{message}；请导入有效配置")


__all__ = ["CustomProfileWidgetView", "build_custom_profile_view"]
