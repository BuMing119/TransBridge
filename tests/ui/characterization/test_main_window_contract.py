from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.window_lifecycle import AutoSaveManager

_APP = QApplication.instance() or QApplication([])


def test_public_main_window_imports_remain_stable() -> None:
    assert MainWindow.__module__ == "transbridge.ui.main_window"
    assert AutoSaveManager.__module__ == "transbridge.ui.shell.window_lifecycle"


def test_autosave_stop_is_idempotent() -> None:
    window = SimpleNamespace(
        context=SimpleNamespace(dirty=False, active_project_id=None, active_variant_id=None),
    )
    manager = AutoSaveManager(window, debounce_ms=1)

    manager.stop()
    manager.stop()

    assert not manager._interval_timer.isActive()
    assert not manager._debounce_timer.isActive()
