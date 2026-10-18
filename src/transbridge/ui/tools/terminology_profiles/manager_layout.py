"""Build the focused scheme manager's controls and editor layout."""

from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .mapping_editor import ProfileMappingEditor


def build_manager_layout(view):
    def button(text, callback):
        result = QPushButton(text, view)
        result.setAutoDefault(False)
        ComponentStyle.apply_static(result, ComponentKind.BUTTON)
        result.clicked.connect(callback)
        return result

    layout = QVBoxLayout(view)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)
    view.context_label = QLabel(f"正在编辑工程：{view._project_name}", view)
    view.context_label.setWordWrap(True)
    layout.addWidget(view.context_label)
    row = QHBoxLayout()
    row.addWidget(QLabel("编辑方案", view))
    view.profile_combo = QComboBox(view)
    view.profile_combo.setAccessibleName("要管理的译名方案")
    view.profile_combo.currentIndexChanged.connect(view._profile_selected)
    ComponentStyle.apply_static(view.profile_combo, ComponentKind.INPUT)
    row.addWidget(view.profile_combo, 1)
    view.create_button = button("新建空白…", view._create_profile)
    view.copy_button = button("复制…", view._copy_profile)
    view.rename_button = button("重命名…", view._rename_profile)
    view.archive_button = button("归档", view._archive_profile)
    view.restore_button = button("恢复", view._restore_profile)
    for item in (view.create_button, view.copy_button, view.rename_button, view.archive_button, view.restore_button):
        row.addWidget(item)
    layout.addLayout(row)
    creation = QHBoxLayout()
    view.base_create_button = button("基于当前项目术语创建方案…", lambda: view._workflows.create_from_base())
    view.base_create_button.setEnabled(view._base_snapshot_reader is not None)
    creation.addWidget(view.base_create_button)
    creation.addStretch(1)
    view.show_archived = QCheckBox("显示已归档", view)
    view.show_archived.toggled.connect(view._filter_changed)
    creation.addWidget(view.show_archived)
    layout.addLayout(creation)
    explanation = QLabel(
        "方案只调整预览、输出和 AI 使用的术语，不修改项目译文。保存修改保留草稿；发布后使用中的版本才会更新。", view
    )
    explanation.setWordWrap(True)
    explanation.setAccessibleName("译名方案映射说明")
    layout.addWidget(explanation)
    search = QHBoxLayout()
    view.mapping_search = QLineEdit(view)
    view.mapping_search.setPlaceholderText("搜索原文、当前译名或方案译名")
    view.mapping_search.setAccessibleName("搜索方案映射")
    view.mapping_search.setClearButtonEnabled(True)
    ComponentStyle.apply_static(view.mapping_search, ComponentKind.INPUT)
    search.addWidget(view.mapping_search, 1)
    view.changed_only = QCheckBox("仅显示与已发布版本不同的条目", view)
    search.addWidget(view.changed_only)
    layout.addLayout(search)
    view.mapping_table = ProfileMappingEditor(view)
    view.mapping_table.dirty_changed.connect(view._edit_changed)
    view.mapping_table.search_status.connect(view._search_status)
    view.mapping_search.textChanged.connect(view.mapping_table.search)
    view.changed_only.toggled.connect(view.mapping_table.show_changed_only)
    layout.addWidget(view.mapping_table, 1)

    def add_mapping():
        view.mapping_search.clear()
        view.changed_only.setChecked(False)
        view.mapping_table.append_mapping()

    mappings = QHBoxLayout()
    view.add_mapping_button = button("+ 添加术语", add_mapping)
    view.remove_mapping_button = button("移除所选映射…", view.mapping_table.remove_selected)
    mappings.addWidget(view.add_mapping_button)
    mappings.addWidget(view.remove_mapping_button)
    mappings.addStretch(1)
    layout.addLayout(mappings)
    view.status_label = QLabel("", view)
    view.status_label.setWordWrap(True)
    view.status_label.setAccessibleName("译名方案管理状态")
    layout.addWidget(view.status_label)
    view.mapping_table.focus_failed.connect(view.status_label.setText)
    footer = QHBoxLayout()
    view.save_button = button("保存修改", view._save_draft)
    view.publish_button = button("预览并发布…", view._publish)
    view.publish_use_button = button("发布并用于当前翻译版本…", lambda: view._workflows.publish(adopt=True))
    view.close_button = button("关闭", view.close)
    footer.addStretch(1)
    for item in (view.save_button, view.publish_button, view.publish_use_button, view.close_button):
        footer.addWidget(item)
    layout.addLayout(footer)
