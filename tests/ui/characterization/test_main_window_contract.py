from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.ui.main_window import MainWindow, _AutoSaveManager

_APP = QApplication.instance() or QApplication([])


def test_public_main_window_imports_remain_stable() -> None:
    assert MainWindow.__module__ == "transbridge.ui.main_window"
    assert _AutoSaveManager.__module__ == "transbridge.ui.main_window"


def test_autosave_stop_is_idempotent() -> None:
    window = SimpleNamespace(
        _ctx=SimpleNamespace(dirty=False, uses_authoritative_projection=False),
    )
    manager = _AutoSaveManager(window, debounce_ms=1)

    manager.stop()
    manager.stop()

    assert not manager._interval_timer.isActive()
    assert not manager._debounce_timer.isActive()
