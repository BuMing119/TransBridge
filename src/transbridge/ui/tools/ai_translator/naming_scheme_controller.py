"""Bind the AI task's naming-scheme controls to the Workbench coordinator."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QSignalBlocker


class AiNamingSchemeBinding(QObject):
    """Share Workbench previews while AI keeps the variant's applied scheme."""

    def __init__(self, controller, controls, refresh_task, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._controls = controls
        self._refresh_task = refresh_task
        self._summary_label = "保持当前译名"
        self._profile_key = None if controller is None else self._task_profile_key(controller.state)
        controls.naming_scheme_combo.currentIndexChanged.connect(self._selection_changed)
        controls.naming_scheme_manage_btn.clicked.connect(self.open_manager)
        if controller is None:
            self._render_unavailable()
            return
        controller.state_changed.connect(self._state_changed)
        self.render(controller.state)

    def render(self, state) -> None:
        combo = self._controls.naming_scheme_combo
        with QSignalBlocker(combo):
            combo.clear()
            combo.addItem("使用项目默认译名", None)
            for choice in state.choices:
                combo.addItem(choice.label, choice.profile_id)
            index = combo.findData(state.selected_choice_id)
            combo.setCurrentIndex(max(index, 0))
        combo.setEnabled(state.enabled)
        self._controls.naming_scheme_manage_btn.setEnabled(state.can_manage)
        applied = state.applied_revision
        applied_name = "项目默认译名" if applied is None else applied.name
        target = state.selected_revision
        target_name = "项目默认译名" if target is None else target.name
        if state.loading:
            status = state.detail
        elif not state.enabled:
            status = "当前工程未启用译名方案；任务将使用现有译文和“术语库”页中的术语来源。"
        elif state.is_preview:
            status = (
                f"工作台正在预览“{target_name}”；当前 AI 任务仍使用“{applied_name}”。"
                "另存为翻译版本并切换到该版本后，可按预览方案继续翻译。"
            )
        else:
            status = f"当前 AI 任务使用翻译版本已关联的“{applied_name}”；任务开始后会固定该修订。"
        self._summary_label = "保持当前译名" if applied is None else applied.name
        self._set_status(status)

    @property
    def summary_label(self) -> str:
        return self._summary_label

    def _selection_changed(self, _index: int) -> None:
        if self._controller is None:
            self._render_unavailable()
            return
        self._controller.select(self._controls.naming_scheme_combo.currentData())

    def _state_changed(self, state) -> None:
        self.render(state)
        key = self._task_profile_key(state)
        if key != self._profile_key:
            self._profile_key = key
            self._refresh_task()

    @staticmethod
    def _task_profile_key(state):
        profile = state.applied_revision
        return (
            None
            if profile is None
            else (profile.project_id, profile.profile_id, profile.revision, profile.content_digest)
        )

    def open_manager(self) -> None:
        if self._controller is not None:
            self._controller.open_manager()

    def _render_unavailable(self) -> None:
        combo = self._controls.naming_scheme_combo
        with QSignalBlocker(combo):
            combo.clear()
            combo.addItem("保持当前译名", None)
        combo.setEnabled(False)
        self._controls.naming_scheme_manage_btn.setEnabled(False)
        self._summary_label = "保持当前译名"
        self._set_status("当前工程未启用译名方案；任务将使用现有译文和“术语库”页中的术语来源。")

    def _set_status(self, text: str) -> None:
        label = self._controls.naming_scheme_status_label
        label.setText(text)
        label.setToolTip(text)
        label.setAccessibleDescription(text)


__all__ = ["AiNamingSchemeBinding"]
