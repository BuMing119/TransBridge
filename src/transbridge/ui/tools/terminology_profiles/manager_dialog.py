"""Focused manager for one Project's terminology localization profiles."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QInputDialog,
    QMessageBox,
    QWidget,
)

from transbridge.application.terminology_profiles import ProfileState

from .edit_prompts import choose_unsaved_action, confirm_archive
from .manager_layout import build_manager_layout
from .manager_workflows import ProfileManagerWorkflows


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
        variant_id: str | None = None,
        base_snapshot_reader=None,
        thread_pool=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._project_id = project_id
        self._profiles = ()
        self._current = None
        self._project_name = project_name or project_id
        self._variant_names = dict(variant_names or {})
        self._variant_id = variant_id
        self._base_snapshot_reader = base_snapshot_reader
        self.context_active = True
        self.setWindowTitle("管理译名方案")
        self.setAccessibleName("译名方案管理")
        self.setProperty("tbTaskDialog", True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(920, 600)
        build_manager_layout(self)
        self._workflows = ProfileManagerWorkflows(
            self, service, project_id, variant_id, base_snapshot_reader, thread_pool=thread_pool
        )
        self.finished.connect(lambda _result: self._workflows.close())
        self.finished.connect(lambda _result: self.mapping_table.close_search())
        self._reload_profiles(select_profile_id=selected_profile_id)

    @property
    def current_profile(self):
        return self._current

    @property
    def variant_names(self):
        return self._variant_names

    def open_intent(self, *, original=None, scope=None, rename=False, create_from_base=False):
        if self._workflows.active:
            return
        if create_from_base:
            self._workflows.create_from_base()
        elif rename:
            self._rename_profile()
        elif original is not None:
            self.changed_only.setChecked(False)
            self.mapping_search.setText(original)
            self.mapping_table.focus_original(original, scope=scope)

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
                suffix = "未发布草稿" if revision is None else "已发布 · 可选择"
                if profile.state is ProfileState.ARCHIVED:
                    suffix = "已归档"
                self.profile_combo.addItem(f"{profile.name} · {suffix}", profile.profile_id)
                row = self.profile_combo.count() - 1
                self.profile_combo.setItemData(row, self.profile_combo.itemText(row), Qt.ItemDataRole.ToolTipRole)
            index = self.profile_combo.findData(select_profile_id)
            self.profile_combo.setCurrentIndex(index if index >= 0 else (0 if self._profiles else -1))
        finally:
            self.profile_combo.blockSignals(False)
        self.profile_combo.setToolTip(self.profile_combo.currentText())
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
            current_id = None if self._current is None else self._current.profile_id
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(current_id))
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
        if self._current is not None and profile_id != self._current.profile_id:
            self.mapping_search.clear()
            self.changed_only.setChecked(False)
        self._current = next((item for item in self._profiles if item.profile_id == profile_id), None)
        published = (
            self._service.published_revision(self._current.profile_id, self._current.latest_published_revision)
            if self._current is not None and self._current.latest_published_revision is not None
            else None
        )
        self.mapping_table.load(
            () if self._current is None else self._current.draft.mappings,
            published=() if published is None else published.content.mappings,
        )
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
        self.publish_use_button.setEnabled(enabled and not archived and self._variant_id is not None)
        self.status_label.setText("请选择或新建译名方案。" if not enabled else self._profile_status(self._current))

    def _profile_status(self, profile) -> str:
        if profile.state is ProfileState.ARCHIVED:
            return "此方案已归档。恢复后可继续编辑；原使用者不会自动重新选择此方案。"
        published = profile.latest_published_revision
        if published is None:
            return "尚未发布。可先保存草稿，或点击“发布并用于当前翻译版本”完成发布与选择。"
        applied = self._service.published_revision(profile.profile_id, published)
        if applied is None or applied.content_digest != profile.draft.content_digest or applied.name != profile.name:
            return "有未发布的修改；当前使用中的翻译版本仍保持上一次发布结果。"
        return "当前修改已发布。下方可将此方案用于当前翻译版本。"

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
        self._changed("已复制为新方案草稿；发布后即可使用。", select_profile_id=profile.profile_id)

    def _rename_profile(self) -> None:
        if self._current is None or self._workflows.active:
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
            message = (
                "已重命名；预览并发布后，使用中的版本将采用新名称。"
                if profile.latest_published_revision is not None
                else "已重命名译名方案。"
            )
            self._changed(message, select_profile_id=profile.profile_id)

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
        self._changed("草稿已保存；发布后才会更新使用中的方案内容。", select_profile_id=profile.profile_id)
        return True

    def _publish(self) -> None:
        self._workflows.publish()

    def _guard_unsaved(self) -> bool:
        if self._workflows.active:
            self.status_label.setText("正在处理方案，请完成当前操作后再关闭或切换。")
            return False
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

    def _search_status(self, pending: bool, error: str) -> None:
        if pending or error:
            self.status_label.setText(error or "正在搜索全部方案映射…")
        elif self._current is not None:
            self._edit_changed(self.mapping_table.is_dirty)

    def context_changed(self) -> None:
        self.context_active = False
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
