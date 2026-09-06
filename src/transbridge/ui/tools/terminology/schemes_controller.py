"""Coordinate naming-scheme actions hosted by the terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QDialog, QMessageBox

from transbridge.ai_translator.term_source_reader import ConfiguredTermSourceReader
from transbridge.ui.tools.terminology_profiles import (
    TerminologySourceImportController,
    TerminologySourcePickerDialog,
)


class TerminologySchemesController(QObject):
    """Bind the project terminology surface to the shared profile controller."""

    def __init__(self, view, context, profile_controller, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self._context = context
        self._profiles = profile_controller
        self._imports = TerminologySourceImportController(
            view,
            view.create_button,
            profile_controller,
            ConfiguredTermSourceReader,
            idle_button_text="从术语来源创建…",
        )
        view.create_requested.connect(self.create_from_source)
        view.manage_requested.connect(self.open_manager)
        view.selection_requested.connect(self.select)
        if profile_controller is None or context is None:
            view.render_unavailable()
            return
        profile_controller.state_changed.connect(view.render)
        view.render(profile_controller.state)

    def create_from_source(self) -> None:
        if self._profiles is None or self._context is None:
            return
        try:
            dialog = TerminologySourcePickerDialog(self._context, self._view)
        except Exception as exc:  # noqa: BLE001 - configuration errors must remain visible
            QMessageBox.warning(self._view, "无法读取术语来源", str(exc))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selection = dialog.selection
        if selection is None:
            return
        self._imports.start_with_reader(
            selection.request,
            default_name=selection.default_name,
            reader_factory=selection.reader_factory,
        )

    def open_manager(self) -> None:
        if self._profiles is not None:
            self._profiles.open_manager()

    def select(self, profile_id: str | None) -> None:
        if self._profiles is not None:
            self._profiles.select(profile_id)


__all__ = ["TerminologySchemesController"]
