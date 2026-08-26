"""Qt dialog and signal adapter for custom workflow profile intents."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QWidget

from transbridge.application.translation.custom_workflow_profile import BaseMode

from .custom_profile_presenter import CustomProfilePresenter
from .custom_profile_view import CustomProfileWidgetView
from .view_controls import TranslatorViewOwner
from .view_state import TranslatorViewPort


class CustomProfileController:
    """Bind profile widgets to a focused presenter, outside the window facade."""

    def __init__(
        self,
        parent: QWidget,
        view: TranslatorViewOwner,
        view_port: TranslatorViewPort,
        config_presenter: object,
        refresh: Callable[[], None],
        *,
        presenter: CustomProfilePresenter | None = None,
    ) -> None:
        self._parent = parent
        self._view = view
        self._view_port = view_port
        self._refresh = refresh
        self._presenter = presenter or CustomProfilePresenter(CustomProfileWidgetView(view), config_presenter)  # type: ignore[arg-type]
        controls = view.controls
        controls.custom_profile_combo.currentIndexChanged.connect(self._on_selected)
        controls.custom_profile_new_btn.clicked.connect(self._on_new)
        controls.custom_profile_rename_btn.clicked.connect(self._on_rename)
        controls.custom_profile_delete_btn.clicked.connect(self._on_delete)
        controls.custom_profile_import_btn.clicked.connect(self._on_import)
        controls.custom_profile_export_btn.clicked.connect(self._on_export)
        controls.custom_base_mode_combo.currentIndexChanged.connect(self._on_base_mode_changed)
        self._presenter.load()

    @property
    def has_selection(self) -> bool:
        return self._presenter.has_selection

    def activate_selected(self) -> None:
        self._presenter.activate_selected()

    def block_unavailable_start(self) -> bool:
        if self._view_port.selected_mode != "custom" or self.has_selection:
            return False
        controls = self._view.controls
        controls.start_btn.setEnabled(False)
        message = "自定义模式没有可用配置，请先新建或导入配置"
        controls.preflight_label.set_full_text(message)
        controls.preflight_label.setToolTip(message)
        controls.preflight_label.setAccessibleDescription(message)
        return True

    def _on_selected(self, _index: int) -> None:
        profile_id = self._view.controls.custom_profile_combo.currentData()
        if isinstance(profile_id, str):
            self._run(lambda: self._presenter.select(profile_id))

    def _on_new(self) -> None:
        name, accepted = QInputDialog.getText(self._parent, "新建自定义工作流", "配置名称:")
        if not accepted:
            return
        base_mode = self._base_mode()
        self._run(lambda: self._presenter.create(name, base_mode))

    def _on_rename(self) -> None:
        selected = self._presenter.selected_profile
        if selected is None:
            return
        name, accepted = QInputDialog.getText(
            self._parent,
            "重命名自定义工作流",
            "配置名称:",
            text=selected.name,
        )
        if accepted:
            self._run(lambda: self._presenter.rename_selected(name))

    def _on_delete(self) -> None:
        selected = self._presenter.selected_profile
        if selected is None:
            return
        answer = QMessageBox.question(
            self._parent,
            "删除自定义工作流",
            f"确定删除“{selected.name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run(self._presenter.delete_selected)

    def _on_import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self._parent,
            "导入自定义工作流",
            "",
            "TransBridge 工作流 (*.json)",
        )
        if path:
            self._run(lambda: self._presenter.import_file(path))

    def _on_export(self) -> None:
        selected = self._presenter.selected_profile
        if selected is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self._parent,
            "导出自定义工作流",
            f"{selected.name}.json",
            "TransBridge 工作流 (*.json)",
        )
        if path:
            self._run(lambda: self._presenter.export_selected(path), success="配置已导出")

    def _on_base_mode_changed(self, _index: int) -> None:
        if self._view_port.selected_mode == "custom" and self.has_selection:
            self._run(lambda: self._presenter.change_base_mode(self._base_mode()))

    def _base_mode(self) -> BaseMode:
        value = self._view.controls.custom_base_mode_combo.currentData()
        return value if value in {"translate", "polish", "mixed"} else "polish"  # type: ignore[return-value]

    def _run(self, operation: Callable[[], object], *, success: str | None = None) -> None:
        try:
            operation()
        except Exception as exc:
            QMessageBox.warning(self._parent, "自定义工作流", str(exc))
            return
        if success is not None:
            QMessageBox.information(self._parent, "自定义工作流", success)
        self._refresh()


__all__ = ["CustomProfileController"]
