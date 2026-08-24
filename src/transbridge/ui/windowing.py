"""Safe presentation helpers for top-level, non-modal Qt windows."""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget


def _activate(window: QWidget) -> None:
    if sip.isdeleted(window):
        return
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()


def show_and_activate(window: QWidget, *, deferred: bool = False) -> QWidget:
    """Show a retained window and request foreground activation.

    ``deferred`` is required when the launcher will close later in the current
    event callback; otherwise that close can return focus to the main window.
    This helper deliberately does not own ``window``. Callers must retain it
    through a QObject parent or a feature-local window registry/field.
    """
    if deferred:
        QTimer.singleShot(0, lambda target=window: _activate(target))
    else:
        _activate(window)
    return window
