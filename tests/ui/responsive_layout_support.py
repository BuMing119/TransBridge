"""Use real font metrics and the application skin for responsive-layout checks."""

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.foundation.visual_style import compile_application_stylesheet


@pytest.fixture(name="layout_app")
def themed_layout_app():
    app = QApplication.instance() or QApplication([])
    font, palette, stylesheet = QFont(app.font()), QPalette(app.palette()), app.styleSheet()
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    if font_id >= 0:
        app.setFont(QFont("Microsoft YaHei", 9))
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(app, create_builtin_registry(), preferences)
    service.start()
    try:
        yield app
    finally:
        # processEvents does not dispatch DeferredDelete outside app.exec().
        # Release closed test windows before restoring the shared application skin.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        service.close()
        app.setStyleSheet(stylesheet)
        app.setPalette(palette)
        app.setFont(font)
        if font_id >= 0:
            QFontDatabase.removeApplicationFont(font_id)


def settle_layout(app):
    for _ in range(5):
        app.processEvents()


def apply_layout_font(widget, snapshot, point_size):
    """Exercise the requested text size in the actual theme, which owns fonts via QSS."""
    primitives = snapshot.tokens.primitives
    typography = replace(primitives.typography, body_size=point_size)
    tokens = replace(snapshot.tokens, primitives=replace(primitives, typography=typography))
    widget.setStyleSheet(compile_application_stylesheet(replace(snapshot, tokens=tokens)))
