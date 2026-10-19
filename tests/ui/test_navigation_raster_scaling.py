from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QWidget
import pytest

from tests.ui.responsive_layout_support import themed_layout_app  # noqa: F401
from transbridge.ui.shell.navigation_rail import NavigationRail, _outlined_asset_icon, _ui_asset_path


@pytest.mark.parametrize("dpr", [1.25, 1.5, 2.0])
def test_navigation_asset_preserves_pixels_for_scaled_displays(layout_app, dpr):
    widget = QWidget()
    try:
        path = _ui_asset_path("paratranz.png")
        assert path is not None
        icon = _outlined_asset_icon(widget, path)
        pixmap = icon.pixmap(QSize(18, 18), dpr)
        assert pixmap.width() >= int(18 * dpr)
        assert pixmap.devicePixelRatioF() == pytest.approx(dpr, abs=0.04)
        assert pixmap.deviceIndependentSize().width() == pytest.approx(18, abs=0.5)
    finally:
        widget.deleteLater()


def test_avatar_keeps_high_resolution_with_logical_size_and_circular_crop(layout_app):
    source = QPixmap(256, 128)
    source.fill(QColor(20, 160, 80))
    source.setDevicePixelRatio(2)
    avatar = NavigationRail._circular_avatar(source)
    assert avatar.deviceIndependentSize().width() == avatar.deviceIndependentSize().height() == 32
    assert avatar.width() >= 64
    pixels = avatar.toImage()
    assert pixels.pixelColor(0, 0).alpha() == 0
    center = pixels.pixelColor(pixels.width() // 2, pixels.height() // 2)
    assert center.green() == 160 and center.alpha() == 255
