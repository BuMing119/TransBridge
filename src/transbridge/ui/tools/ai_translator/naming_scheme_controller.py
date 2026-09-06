"""Bind the AI task's naming-scheme controls to the Workbench coordinator."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QSignalBlocker


class AiNamingSchemeBinding(QObject):
    """Render and forward the shared Project/Variant naming-scheme selection."""

    def __init__(self, controller, controls, refresh_task, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._controls = controls
        self._refresh_task = refresh_task
        self._summary_label = "保持当前译名"
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
            combo.addItem("保持当前译名", None)
            for choice in state.choices:
                combo.addItem(choice.label, choice.profile_id)
            index = combo.findData(state.selected_profile_id)
            combo.setCurrentIndex(max(index, 0))
        combo.setEnabled(state.enabled)
        self._controls.naming_scheme_manage_btn.setEnabled(state.can_manage)
        selected = next(
            (choice for choice in state.choices if choice.profile_id == state.selected_profile_id),
            None,
        )
        if not state.enabled:
            status = "当前工程未启用译名方案；任务将使用现有译文和“术语库”页中的术语来源。"
        elif selected is None:
            status = "保持项目译文中的现有译名；选择方案会同步工作台，并影响之后创建的任务与导出。"
        else:
            status = f"本次采用“{selected.label}”；已与工作台同步，任务开始后会固定当前版本。"
        self._summary_label = "保持当前译名" if selected is None else selected.label
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
        self._refresh_task()

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
