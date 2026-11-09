# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Coordinate naming-scheme actions hosted by the terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from transbridge.ai_translator.term_source_reader import ConfiguredTermSourceReader
from transbridge.application.projects import ParaTranzTargetStatus
from transbridge.ui.paratranz.target_context import resolved_paratranz_target
from transbridge.ui.tools.terminology_profiles import (
    TerminologyImportPolicyDialog,
    TerminologySourceImportController,
)
from transbridge.ui.tools.terminology_profiles.source_catalog import bound_paratranz_selection, local_file_selection


class TerminologySchemesController(QObject):
    """Bind the project terminology surface to the shared profile controller."""

    def __init__(self, view, context, profile_controller, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self._context = context
        self._profiles = profile_controller
        self._imports = TerminologySourceImportController(
            view,
            view.import_button,
            profile_controller,
            ConfiguredTermSourceReader,
            idle_button_text="导入术语…",
            select_after_create=True,
        )
        self._imports.busy_changed.connect(view.set_import_busy)
        view.create_requested.connect(self.create_from_source)
        view.paratranz_requested.connect(self.create_from_paratranz)
        view.create_from_base_requested.connect(self.create_from_base)
        view.rename_requested.connect(self.rename)
        view.manage_requested.connect(self.open_manager)
        view.selection_requested.connect(self.select)
        if profile_controller is None or context is None:
            view.render_unavailable()
            return
        profile_controller.state_changed.connect(view.render)
        view.render(profile_controller.state)
        self._refresh_paratranz()
        view.actions_menu.aboutToShow.connect(self._refresh_paratranz)
        for name in ("paratranz_binding_changed", "config_changed", "user_changed"):
            signal = getattr(context, name, None)
            if signal is not None:
                signal.connect(self._refresh_paratranz)

    def close(self) -> None:
        self._imports.dispose()
        self._profiles = None
        self._context = None

    def create_from_source(self) -> None:
        if self._profiles is None or self._context is None or self._imports.busy:
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._view,
            "选择术语文件",
            "",
            "术语文件 (*.json *.csv *.xls *.xlsx);;JSON (*.json);;CSV (*.csv);;Excel (*.xls *.xlsx)",
        )
        if not path:
            return
        try:
            selection = local_file_selection(path)
        except Exception as exc:  # noqa: BLE001 - configuration errors must remain visible
            QMessageBox.warning(self._view, "无法使用术语文件", str(exc))
            return
        self._confirm_import(selection)

    def create_from_paratranz(self) -> None:
        if self._profiles is None or self._context is None or self._imports.busy:
            return
        try:
            selection = bound_paratranz_selection(self._context)
        except Exception as exc:  # noqa: BLE001 - target/configuration errors must remain visible
            QMessageBox.warning(self._view, "无法读取 ParaTranz 术语", str(exc))
            return
        if selection is None:
            self._refresh_paratranz()
            QMessageBox.warning(self._view, "无法导入 ParaTranz 术语", self._view.paratranz_button.toolTip())
            return
        self._confirm_import(selection)

    def _confirm_import(self, selection) -> None:
        dialog = TerminologyImportPolicyDialog(selection, self._view)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if selection.request.source_id == "paratranz":
            try:
                current = resolved_paratranz_target(self._context)
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(self._view, "ParaTranz 绑定不可用", str(exc))
                return
            if current.project_id != selection.request.paratranz_project_id or current.status not in {
                ParaTranzTargetStatus.AVAILABLE,
                ParaTranzTargetStatus.UNVERIFIED,
            }:
                QMessageBox.warning(self._view, "ParaTranz 绑定已变化", "请重新选择当前工程的 ParaTranz 导入。")
                return
        self._imports.start_with_reader(
            selection.request,
            default_name=selection.default_name,
            reader_factory=selection.reader_factory,
            policy=dialog.policy,
        )

    def _refresh_paratranz(self, *_args) -> None:
        if self._context is None:
            return
        try:
            target = resolved_paratranz_target(self._context)
        except (ValueError, TypeError) as exc:
            self._view.set_paratranz_target(False, bound=True, detail=f"ParaTranz 绑定不可用：{exc}")
            return
        available = target.project_id is not None and target.status in {
            ParaTranzTargetStatus.AVAILABLE,
            ParaTranzTargetStatus.UNVERIFIED,
        }
        self._view.set_paratranz_target(
            available,
            bound=target.project_id is not None,
            detail=(
                f"读取当前工程绑定的 ParaTranz 项目：{target.project_name or target.project_id}"
                if available
                else "请先绑定当前工程的 ParaTranz 项目"
                if target.status is ParaTranzTargetStatus.UNBOUND
                else target.reason or "当前工程的 ParaTranz 绑定暂不可用。"
            ),
        )

    def open_manager(self) -> None:
        if self._profiles is not None:
            self._profiles.open_manager()

    def create_from_base(self) -> None:
        if self._profiles is not None:
            self._profiles.open_manager(create_from_base=True)

    def rename(self) -> None:
        if self._profiles is not None:
            self._profiles.open_manager(rename=True)

    def select(self, profile_id: str | None) -> None:
        if self._profiles is not None:
            self._profiles.select(profile_id)


__all__ = ["TerminologySchemesController"]
