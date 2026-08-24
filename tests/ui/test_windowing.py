from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.ui.windowing import show_and_activate

_APP = QApplication.instance() or QApplication([])


class _TrackingWindow(QWidget):
    def __init__(self, *, minimized: bool = False) -> None:
        super().__init__()
        self.minimized = minimized
        self.calls: list[str] = []

    def isMinimized(self) -> bool:
        return self.minimized

    def show(self) -> None:
        self.calls.append("show")

    def showNormal(self) -> None:
        self.calls.append("showNormal")

    def raise_(self) -> None:
        self.calls.append("raise")

    def activateWindow(self) -> None:
        self.calls.append("activate")


def test_show_and_activate_preserves_normal_window_state() -> None:
    window = _TrackingWindow()

    assert show_and_activate(window) is window

    assert window.calls == ["show", "raise", "activate"]


def test_show_and_activate_restores_minimized_window() -> None:
    window = _TrackingWindow(minimized=True)

    show_and_activate(window)

    assert window.calls == ["showNormal", "raise", "activate"]


def test_deferred_activation_waits_for_launcher_callback_to_finish() -> None:
    window = _TrackingWindow()

    show_and_activate(window, deferred=True)

    assert window.calls == []
    _APP.processEvents()
    assert window.calls == ["show", "raise", "activate"]
