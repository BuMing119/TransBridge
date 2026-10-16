"""Focused manager for one Project's terminology localization profiles."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology_profiles import ProfileState
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .edit_prompts import choose_unsaved_action, confirm_archive
from .mapping_editor import ProfileMappingEditor


class TerminologyProfileManagerDialog(QDialog):
    """Edit profile mappings while preserving advanced projection metadata."""

    profiles_changed = pyqtSignal()

    def __init__(
        self,
        service,
        project_id: str,
        parent: QWidget | None = None,
        *,
        selected_profile_id: str | None = None,
        project_name: str | None = None,
        variant_names: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._project_id = project_id
        self._profiles = ()
        self._current = None
        self._project_name = project_name or project_id
        self._variant_names = dict(variant_names or {})
        self.setWindowTitle("管理译名方案")
        self.setAccessibleName("译名方案管理")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(920, 600)
        self._init_ui()
        self._reload_profiles(select_profile_id=selected_profile_id)

    @property
    def current_profile(self):
        return self._current

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        self.context_label = QLabel(f"正在编辑工程：{self._project_name}", self)
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("译名方案 ·", self))
        self.profile_combo = QComboBox(self)
        self.profile_combo.setAccessibleName("要管理的译名方案")
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)
        ComponentStyle.apply_static(self.profile_combo, ComponentKind.INPUT)
        profile_row.addWidget(self.profile_combo, 1)
        self.create_button = self._button("新建…", self._create_profile)
        self.copy_button = self._button("复制…", self._copy_profile)
        self.rename_button = self._button("重命名…", self._rename_profile)
        self.archive_button = self._button("归档", self._archive_profile)
        self.restore_button = self._button("恢复", self._restore_profile)
        for button in (
            self.create_button,
            self.copy_button,
            self.rename_button,
            self.archive_button,
            self.restore_button,
        ):
            profile_row.addWidget(button)
        layout.addLayout(profile_row)
        self.show_archived = QCheckBox("显示已归档", self)
        self.show_archived.toggled.connect(self._filter_changed)
        layout.addWidget(self.show_archived)

        explanation = QLabel(
            "为同一项目制作不同的译名方案。切换方案会调整预览、输出和 AI 使用的已登记术语，不会修改项目译文。"
            "此表只编辑基础映射；条目特例和已确认位置会保持不变。",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setAccessibleName("译名方案映射说明")
        layout.addWidget(explanation)

        self.mapping_table = ProfileMappingEditor(self)
        self.mapping_table.dirty_changed.connect(self._edit_changed)
        layout.addWidget(self.mapping_table, 1)

        mapping_actions = QHBoxLayout()
        self.add_mapping_button = self._button("+ 添加术语", lambda: self.mapping_table.append_mapping())
        self.remove_mapping_button = self._button("移除选中术语", self.mapping_table.remove_selected)
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

    def _reload_profiles(self, *, select_profile_id: str | None = None, preserve_editor: bool = False) -> None:
        try:
            self._profiles = self._service.list_profiles(
                self._project_id, include_archived=self.show_archived.isChecked()
            )
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
                if profile.state is ProfileState.ARCHIVED:
                    suffix = "已归档"
                self.profile_combo.addItem(f"{profile.name} · {suffix}", profile.profile_id)
            index = self.profile_combo.findData(select_profile_id)
            self.profile_combo.setCurrentIndex(index if index >= 0 else (0 if self._profiles else -1))
        finally:
            self.profile_combo.blockSignals(False)
        if not preserve_editor:
            self._load_selected_profile()

    def _profile_selected(self, _index: int) -> None:
        selected_id = self.profile_combo.currentData()
        self.select_profile(selected_id)

    def select_profile(self, profile_id: str | None) -> bool:
        if self._current is not None and self._current.profile_id == profile_id:
            return True
        if not self._guard_unsaved():
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(self._current.profile_id))
            self.profile_combo.blockSignals(False)
            return False
        self._reload_profiles(select_profile_id=profile_id)
        return True

    def _filter_changed(self, checked: bool) -> None:
        if not self._guard_unsaved():
            self.show_archived.blockSignals(True)
            self.show_archived.setChecked(not checked)
            self.show_archived.blockSignals(False)
            return
        self._reload_profiles()

    def _load_selected_profile(self) -> None:
        profile_id = self.profile_combo.currentData()
        self._current = next((item for item in self._profiles if item.profile_id == profile_id), None)
        self.mapping_table.load(() if self._current is None else self._current.draft.mappings)
        enabled = self._current is not None
        archived = enabled and self._current.state is ProfileState.ARCHIVED
        self.copy_button.setEnabled(enabled)
        self.rename_button.setEnabled(enabled)
        self.archive_button.setVisible(not archived)
        self.archive_button.setEnabled(enabled)
        self.restore_button.setVisible(bool(archived))
        for widget in (
            self.add_mapping_button,
            self.remove_mapping_button,
            self.mapping_table,
            self.save_button,
            self.publish_button,
        ):
            widget.setEnabled(enabled and not archived)
        self.status_label.setText("请选择或新建译名方案。" if not enabled else self._profile_status(self._current))

    def _profile_status(self, profile) -> str:
        if profile.state is ProfileState.ARCHIVED:
            return "此方案已归档。恢复后可继续编辑；原使用者不会自动重新选择此方案。"
        published = profile.latest_published_revision
        if published is None:
            return "尚未应用；保存后点击“应用修改”，即可在工作台和 AI 翻译中选择。"
        applied = self._service.published_revision(profile.profile_id, published)
        if applied is None or applied.content_digest != profile.draft.content_digest:
            return "有尚未应用的修改；当前使用中的翻译版本仍保持上一次结果。"
        return "当前修改已应用，可在工作台和 AI 翻译中选择。"

    def _create_profile(self) -> None:
        if not self._guard_unsaved():
            return
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
        if self._current is None or not self._guard_unsaved():
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
        self.mapping_table.commit_pending_edits()
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
        if self.mapping_table.is_dirty:
            # A concurrent content change must still fail the subsequent save CAS.
            if profile.draft != self._current.draft:
                profile = replace(profile, draft=self._current.draft, draft_revision=self._current.draft_revision)
            self._current = profile
            self._reload_profiles(select_profile_id=profile.profile_id, preserve_editor=True)
            self._edit_changed(True)
            self.profiles_changed.emit()
        else:
            self._changed("已重命名译名方案。", select_profile_id=profile.profile_id)

    def _archive_profile(self) -> None:
        if self._current is None or not self._guard_unsaved():
            return
        try:
            affected = self._service.list_profile_selections(self._current.profile_id)
            if not confirm_archive(self, self._current.name, affected, self._variant_names):
                return
            self._service.archive(self._current.profile_id, expected_selections=affected)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._current = None
        self._changed("已归档；受影响的翻译版本已使用项目默认译名。勾选“显示已归档”可恢复。")

    def _restore_profile(self) -> None:
        if self._current is None:
            return
        try:
            profile = self._service.restore(self._current.profile_id)
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return
        self._changed("已恢复；请在需要使用的翻译版本中重新选择此方案。", select_profile_id=profile.profile_id)

    def _save_draft(self) -> bool:
        if self._current is None:
            return False
        try:
            mappings = self.mapping_table.read_mappings()
            content = replace(self._current.draft, mappings=mappings)
            profile = self._service.save_draft(
                self._current.profile_id,
                content,
                expected_revision=self._current.draft_revision,
            )
        except Exception as exc:  # noqa: BLE001 - UI adapter boundary
            self._show_error(exc)
            return False
        self._changed("修改已保存；应用修改后才会进入选择列表。", select_profile_id=profile.profile_id)
        return True

    def _publish(self) -> None:
        if self._current is None:
            return
        try:
            mappings = self.mapping_table.read_mappings()
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

    def _guard_unsaved(self) -> bool:
        self.mapping_table.commit_pending_edits()
        if self._current is None or not self.mapping_table.is_dirty:
            return True
        choice = choose_unsaved_action(self, self._current.name)
        if choice == QMessageBox.StandardButton.Save:
            return self._save_draft()
        return choice == QMessageBox.StandardButton.Discard

    def _edit_changed(self, dirty: bool) -> None:
        if self._current is not None:
            self.status_label.setText("有未保存的修改。" if dirty else self._profile_status(self._current))

    def context_changed(self) -> None:
        self.context_label.setText(f"工作区已切换。此窗口仍编辑原工程：{self._project_name}。保存只写入原工程。")
        self.close()

    def reject(self) -> None:
        if self._guard_unsaved():
            super().reject()

    def closeEvent(self, event) -> None:
        if self._guard_unsaved():
            event.accept()
            super().reject()
        else:
            event.ignore()

    def _changed(self, message: str, *, select_profile_id: str | None = None) -> None:
        self._reload_profiles(select_profile_id=select_profile_id)
        self.status_label.setText(message)
        self.profiles_changed.emit()

    def _show_error(self, error: Exception) -> None:
        self.status_label.setText(f"操作失败：{error}")
        QMessageBox.warning(self, "译名方案", str(error))


__all__ = ["TerminologyProfileManagerDialog"]
