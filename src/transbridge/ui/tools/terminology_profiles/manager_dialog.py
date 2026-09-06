"""Focused manager for one Project's terminology localization profiles."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology_profiles import ProfileTermMapping
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologyProfileManagerDialog(QDialog):
    """Edit profile mappings while preserving advanced projection metadata."""

    profiles_changed = pyqtSignal()

    def __init__(self, service, project_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._project_id = project_id
        self._profiles = ()
        self._current = None
        self.setWindowTitle("管理译名方案")
        self.setAccessibleName("译名方案管理")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(920, 600)
        self._init_ui()
        self._reload_profiles()

    @property
    def current_profile(self):
        return self._current

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("方案 ·", self))
        self.profile_combo = QComboBox(self)
        self.profile_combo.setAccessibleName("要管理的译名方案")
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)
        ComponentStyle.apply_static(self.profile_combo, ComponentKind.INPUT)
        profile_row.addWidget(self.profile_combo, 1)
        self.create_button = self._button("新建…", self._create_profile)
        self.copy_button = self._button("复制…", self._copy_profile)
        self.rename_button = self._button("重命名…", self._rename_profile)
        self.archive_button = self._button("归档", self._archive_profile)
        for button in (self.create_button, self.copy_button, self.rename_button, self.archive_button):
            profile_row.addWidget(button)
        layout.addLayout(profile_row)

        explanation = QLabel(
            "为同一术语设置不同场景下采用的译名。切换方案只改变预览和输出，不会修改项目中的普通译文。"
            "此表只编辑基础映射；条目特例和已确认位置会保持不变。",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setAccessibleName("译名方案映射说明")
        layout.addWidget(explanation)

        self.mapping_table = QTableWidget(0, 5, self)
        self.mapping_table.setAccessibleName("译名方案映射")
        self.mapping_table.setHorizontalHeaderLabels([
            "原文术语",
            "当前译文中的叫法",
            "此方案采用的译名",
            "生效范围",
            "插件文件",
        ])
        self.mapping_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mapping_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.mapping_table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.mapping_table.setColumnWidth(4, 150)
        ComponentStyle.apply_static(self.mapping_table, ComponentKind.TABLE)
        layout.addWidget(self.mapping_table, 1)

        mapping_actions = QHBoxLayout()
        self.add_mapping_button = self._button("+ 添加术语", lambda: self._append_mapping())
        self.remove_mapping_button = self._button("移除选中术语", self._remove_mapping)
        mapping_actions.addWidget(self.add_mapping_button)
        mapping_actions.addWidget(self.remove_mapping_button)
        mapping_actions.addStretch(1)
        layout.addLayout(mapping_actions)

        footer = QHBoxLayout()
        self.status_label = QLabel("", self)
        self.status_label.setAccessibleName("译名方案管理状态")
        footer.addWidget(self.status_label, 1)
        self.save_button = self._button("保存修改", self._save_draft)
        self.publish_button = self._button("应用修改", self._publish)
        self.close_button = self._button("关闭", self.close)
        footer.addWidget(self.save_button)
        footer.addWidget(self.publish_button)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

    def _button(self, text: str, callback) -> QPushButton:
        button = QPushButton(text, self)
        ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        button.clicked.connect(callback)
        return button

    def _reload_profiles(self, *, select_profile_id: str | None = None) -> None:
        try:
            self._profiles = self._service.list_profiles(self._project_id)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            self._profiles = ()
        if select_profile_id is None and self._current is not None:
            select_profile_id = self._current.profile_id
        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            for profile in self._profiles:
                revision = profile.latest_published_revision
                suffix = "尚未应用" if revision is None else "可选择"
                self.profile_combo.addItem(f"{profile.name} · {suffix}", profile.profile_id)
            index = self.profile_combo.findData(select_profile_id)
            self.profile_combo.setCurrentIndex(index if index >= 0 else (0 if self._profiles else -1))
        finally:
            self.profile_combo.blockSignals(False)
        self._load_selected_profile()

    def _profile_selected(self, _index: int) -> None:
        self._load_selected_profile()

    def _load_selected_profile(self) -> None:
        profile_id = self.profile_combo.currentData()
        self._current = next((item for item in self._profiles if item.profile_id == profile_id), None)
        self.mapping_table.setRowCount(0)
        if self._current is not None:
            for mapping in self._current.draft.mappings:
                self._append_mapping(mapping)
        enabled = self._current is not None
        for widget in (
            self.copy_button,
            self.rename_button,
            self.archive_button,
            self.add_mapping_button,
            self.remove_mapping_button,
            self.mapping_table,
            self.save_button,
            self.publish_button,
        ):
            widget.setEnabled(enabled)
        self.status_label.setText("请选择或新建译名方案。" if not enabled else self._profile_status(self._current))

    def _profile_status(self, profile) -> str:
        published = profile.latest_published_revision
        if published is None:
            return "尚未应用；保存后点击“应用修改”，即可在工作台和 AI 翻译中选择。"
        applied = self._service.published_revision(profile.profile_id, published)
        if applied is None or applied.content_digest != profile.draft.content_digest:
            return "有尚未应用的修改；当前使用中的翻译版本仍保持上一次结果。"
        return "当前修改已应用，可在工作台和 AI 翻译中选择。"

    def _append_mapping(self, mapping=None) -> None:
        row = self.mapping_table.rowCount()
        self.mapping_table.insertRow(row)
        values = (
            "" if mapping is None else mapping.original,
            "" if mapping is None else mapping.base_translation,
            "" if mapping is None else mapping.translation,
        )
        for column, value in enumerate(values):
            self.mapping_table.setItem(row, column, QTableWidgetItem(value))
        scope = QComboBox(self.mapping_table)
        scope.addItem("整个工程", "project")
        scope.addItem("仅指定插件", "plugin")
        if mapping is not None and mapping.scope_kind == "plugin":
            scope.setCurrentIndex(1)
        scope.currentIndexChanged.connect(lambda _index, widget=scope: self._scope_widget_changed(widget))
        self.mapping_table.setCellWidget(row, 3, scope)
        plugin_id = "" if mapping is None or mapping.plugin_id is None else mapping.plugin_id
        self.mapping_table.setItem(row, 4, QTableWidgetItem(plugin_id))
        self._scope_changed(row)

    def _scope_widget_changed(self, scope: QComboBox) -> None:
        row = self.mapping_table.indexAt(scope.pos()).row()
        if row >= 0:
            self._scope_changed(row)

    def _scope_changed(self, row: int) -> None:
        scope = self.mapping_table.cellWidget(row, 3)
        plugin_item = self.mapping_table.item(row, 4)
        if not isinstance(scope, QComboBox) or plugin_item is None:
            return
        is_plugin = scope.currentData() == "plugin"
        flags = plugin_item.flags()
        plugin_item.setFlags(flags | Qt.ItemFlag.ItemIsEditable if is_plugin else flags & ~Qt.ItemFlag.ItemIsEditable)
        if not is_plugin:
            plugin_item.setText("")

    def _remove_mapping(self) -> None:
        row = self.mapping_table.currentRow()
        if row >= 0:
            self.mapping_table.removeRow(row)

    def _create_profile(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建译名方案", "方案名称:")
        if not accepted:
            return
        try:
            profile = self._service.create(self._project_id, name)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._changed("已创建译名方案。", select_profile_id=profile.profile_id)

    def _copy_profile(self) -> None:
        if self._current is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "复制译名方案",
            "新方案名称:",
            text=f"{self._current.name} 副本",
        )
        if not accepted:
            return
        try:
            profile = self._service.create(self._project_id, name, copy_from=self._current.profile_id)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._changed("已复制为新方案；应用修改后即可使用。", select_profile_id=profile.profile_id)

    def _rename_profile(self) -> None:
        if self._current is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "重命名译名方案",
            "方案名称:",
            text=self._current.name,
        )
        if not accepted:
            return
        try:
            profile = self._service.rename(self._current.profile_id, name)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._changed("已重命名译名方案。", select_profile_id=profile.profile_id)

    def _archive_profile(self) -> None:
        if self._current is None:
            return
        try:
            self._service.archive(self._current.profile_id)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._current = None
        self._changed("已归档译名方案；使用它的翻译版本已回到项目译文。")

    def _save_draft(self) -> None:
        if self._current is None:
            return
        try:
            mappings = self._read_mappings()
            content = replace(self._current.draft, mappings=mappings)
            profile = self._service.save_draft(
                self._current.profile_id,
                content,
                expected_revision=self._current.draft_revision,
            )
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._changed("修改已保存；应用修改后才会进入选择列表。", select_profile_id=profile.profile_id)

    def _publish(self) -> None:
        if self._current is None:
            return
        try:
            mappings = self._read_mappings()
            if mappings != self._current.draft.mappings:
                content = replace(self._current.draft, mappings=mappings)
                self._current = self._service.save_draft(
                    self._current.profile_id,
                    content,
                    expected_revision=self._current.draft_revision,
                )
            published = self._service.publish(
                self._current.profile_id,
                expected_draft_revision=self._current.draft_revision,
            )
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._changed(
            f"已应用“{published.name}”的修改；正在使用该方案的翻译版本已同步更新。",
            select_profile_id=published.profile_id,
        )

    def _read_mappings(self) -> tuple[ProfileTermMapping, ...]:
        mappings = []
        for row in range(self.mapping_table.rowCount()):
            source = self._cell_text(row, 0)
            base = self._cell_text(row, 1)
            target = self._cell_text(row, 2)
            scope_widget = self.mapping_table.cellWidget(row, 3)
            scope = "project" if not isinstance(scope_widget, QComboBox) else str(scope_widget.currentData())
            plugin_id = self._cell_text(row, 4) if scope == "plugin" else None
            mappings.append(
                ProfileTermMapping(
                    source,
                    target,
                    base,
                    scope_kind=scope,
                    plugin_id=plugin_id,
                )
            )
        return tuple(mappings)

    def _cell_text(self, row: int, column: int) -> str:
        item = self.mapping_table.item(row, column)
        return "" if item is None else item.text().strip()

    def _changed(self, message: str, *, select_profile_id: str | None = None) -> None:
        self._reload_profiles(select_profile_id=select_profile_id)
        self.status_label.setText(message)
        self.profiles_changed.emit()

    def _show_error(self, error: Exception) -> None:
        self.status_label.setText(f"操作失败：{error}")
        QMessageBox.warning(self, "译名方案", str(error))


__all__ = ["TerminologyProfileManagerDialog"]
