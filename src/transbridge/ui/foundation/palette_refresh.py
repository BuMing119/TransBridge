"""Refresh QSS palette roles without replacing the application stylesheet."""

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget


def _depth(widget: QWidget) -> int:
    if sip.isdeleted(widget):
        return -1
    depth = 0
    parent = widget.parentWidget()
    while parent is not None and not sip.isdeleted(parent):
        depth += 1
        parent = parent.parentWidget()
    return depth


def refresh_palette_styles(application: QApplication) -> None:
    style = application.style()
    style.unpolish(application)
    style.polish(application)
    # QSS also installs per-widget palettes used by item delegates and native
    # viewports. Restore and resolve these in parent order so inherited roles
    # follow the new application palette while explicit local roles survive.
    widgets = [
        widget
        for widget in application.allWidgets()
        if not sip.isdeleted(widget) and widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
    ]
    widgets.sort(key=_depth)
    # Palette-change handlers may remove a later sibling from this snapshot.
    for widget in widgets:
        if sip.isdeleted(widget):
            continue
        widget_style = widget.style()
        widget_style.unpolish(widget)
        if sip.isdeleted(widget):
            continue
        widget.style().polish(widget)
        if not sip.isdeleted(widget):
            widget.update()
