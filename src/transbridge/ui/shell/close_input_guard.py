"""Temporarily stop new user actions while asynchronous application close drains."""

from __future__ import annotations

import weakref

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication


class CloseInputGuard:
    def __init__(self) -> None:
        self._windows: list[weakref.ReferenceType] = []

    def suspend(self) -> None:
        for window in QApplication.topLevelWidgets():
            # A worker result may require a modal confirmation. Keep it usable;
            # the lifecycle waits for it before saving or disposing resources.
            if window.isVisible() and window.isEnabled() and window.windowModality() == Qt.WindowModality.NonModal:
                self._windows.append(weakref.ref(window))
                window.setEnabled(False)

    @property
    def awaiting_dialog(self) -> bool:
        return QApplication.activeModalWidget() is not None

    def resume(self) -> None:
        windows, self._windows = self._windows, []
        for reference in windows:
            window = reference()
            if window is not None and not sip.isdeleted(window):
                window.setEnabled(True)
