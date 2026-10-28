"""Open historical AI reports through the report browser."""

from __future__ import annotations

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windowing import show_and_activate


def open_report_history(parent: object, theme_view: ThemeView | None) -> object:
    from ._report_history_dialog import _ReportHistoryDialog

    dialog = _ReportHistoryDialog(parent=parent, theme_view=theme_view)
    show_and_activate(dialog)
    return dialog
