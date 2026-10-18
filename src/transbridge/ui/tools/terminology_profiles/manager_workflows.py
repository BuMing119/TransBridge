"""Background publication review and creation from project terminology."""

from dataclasses import replace

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QInputDialog

from transbridge.ui.tools.terminology.async_actions import AsyncActionRunner

from .publication_dialog import confirm_publication


class ProfileManagerWorkflows(QObject):
    def __init__(self, owner, service, project_id, variant_id, base_snapshot_reader, *, thread_pool=None):
        super().__init__(owner)
        self._owner, self._service = owner, service
        self._project_id, self._variant_id = project_id, variant_id
        self._read_base = base_snapshot_reader
        self.active = False
        self._runner = AsyncActionRunner(self, on_error=self._failed, thread_pool=thread_pool)

    def publish(self, *, adopt=False):
        profile = self._owner.current_profile
        if self.active or profile is None or not self._owner.context_active:
            return
        if adopt and self._variant_id is None:
            self._owner.status_label.setText("请从具体翻译版本打开方案管理，再选择使用方案。")
            return
        try:
            content = replace(profile.draft, mappings=self._owner.mapping_table.read_mappings())
        except Exception as exc:  # noqa: BLE001 - invalid editor content remains editable
            self._owner._show_error(exc)
            return
        self._start(
            lambda: self._service.preview_publication(
                profile.profile_id,
                content,
                expected_revision=profile.draft_revision,
                target_variant_id=self._variant_id if adopt else None,
            ),
            self._review,
            "正在计算发布差异和影响范围…",
        )

    def _review(self, preview):
        if not self._owner.context_active:
            self._settle()
            return
        if not preview.has_changes and preview.target_variant_id is None:
            self._settle()
            suffix = "仍有未保存的草稿调整，可保存或放弃。" if self._owner.mapping_table.is_dirty else ""
            self._owner.status_label.setText(f"与已发布方案一致，没有需要发布的修改。{suffix}")
            return
        self._owner.setEnabled(True)
        if not confirm_publication(self._owner, preview, self._owner.variant_names):
            self._settle()
            return
        if not self._owner.context_active:
            self._settle()
            return
        self._owner.status_label.setText("正在发布译名方案…")
        self._owner.setEnabled(False)
        self._runner.run(
            lambda: self._service.publish_reviewed(preview),
            lambda published: self._published(published, preview),
        )

    def _published(self, published, preview):
        self.active = False
        self._owner.setEnabled(True)
        message = (
            f"“{published.name}”已用于当前翻译版本。"
            if preview.target_variant_id is not None
            else f"已发布“{published.name}”；{len(preview.selections)} 个使用中的翻译版本已更新。"
        )
        self._owner._changed(message, select_profile_id=published.profile_id)
        if not self._owner.context_active:
            self._owner.close()

    def create_from_base(self):
        if self.active or not self._owner.context_active or not self._owner._guard_unsaved():
            return
        if self._read_base is None or self._variant_id is None:
            self._owner.status_label.setText("请从项目翻译版本打开方案管理，以便复制当前项目术语。")
            return
        name, accepted = QInputDialog.getText(self._owner, "基于项目术语创建方案", "新方案名称：")
        if not accepted:
            return
        if not name.strip():
            self._owner._show_error(ValueError("方案名称不能为空。"))
            return
        self._start(self._read_base, lambda snapshot: self._base_ready(snapshot, name), "正在读取已发布项目术语…")

    def _base_ready(self, snapshot, name):
        if not self._owner.context_active:
            self._settle()
            return
        self._runner.run(
            lambda: self._service.create_from_project_terminology(self._project_id, self._variant_id, name, snapshot),
            self._created,
        )

    def _created(self, profile):
        self._settle()
        if self._owner.context_active:
            self._owner._changed(
                f"已复制 {len(profile.draft.mappings)} 条项目术语到“{profile.name}”。修改后发布即可使用。",
                select_profile_id=profile.profile_id,
            )

    def _start(self, command, ready, message):
        self.active = True
        self._owner.setEnabled(False)
        self._owner.status_label.setText(message)
        self._runner.run(command, ready)

    def _settle(self):
        self.active = False
        self._owner.setEnabled(True)
        if not self._owner.context_active:
            self._owner.close()

    def _failed(self, error):
        self.active = False
        self._owner.setEnabled(True)
        self._owner._show_error(error)
        if not self._owner.context_active:
            self._owner.close()

    def close(self):
        self._runner.close()
