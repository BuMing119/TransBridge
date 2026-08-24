from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from transbridge.ui.app import _application_icon_path, _apply_application_icon


def test_source_application_icon_is_resolved_and_applied() -> None:
    application = QApplication.instance() or QApplication([])
    original_icon = QIcon(application.windowIcon())
    icon_path = _application_icon_path()

    assert icon_path is not None
    assert icon_path.name == "transbridge.ico"
    assert icon_path.parent.name == "assets"
    assert icon_path.is_file()

    try:
        _apply_application_icon(application)
        assert not application.windowIcon().isNull()
    finally:
        application.setWindowIcon(original_icon)
