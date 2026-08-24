"""Small, theme-aware subset of Tabler outline icons used by the desktop shell.

The SVG path data in this module comes from Tabler Icons:
https://github.com/tabler/tabler-icons

MIT License

Copyright (c) 2020-2026 Paweł Kuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

from html import escape

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget

_ICON_PATHS: dict[str, tuple[str, ...]] = {
    "layout-dashboard": (
        "M5 4h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-6a1 1 0 0 1 1 -1",
        "M5 16h4a1 1 0 0 1 1 1v2a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-2a1 1 0 0 1 1 -1",
        "M15 12h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-6a1 1 0 0 1 1 -1",
        "M15 4h4a1 1 0 0 1 1 1v2a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-2a1 1 0 0 1 1 -1",
    ),
    "language": (
        "M9 6.371c0 4.418 -2.239 6.629 -5 6.629",
        "M4 6.371h7",
        "M5 9c0 2.144 2.252 3.908 6 4",
        "M12 20l4 -9l4 9",
        "M19.1 18h-6.2",
        "M6.694 3l.793 .582",
    ),
    "plus": ("M12 5l0 14", "M5 12l14 0"),
    "list-details": (
        "M13 5h8",
        "M13 9h5",
        "M13 15h8",
        "M13 19h5",
        "M3 5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4",
        "M3 15a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4",
    ),
    "circle-dashed": (
        "M8.56 3.69a9 9 0 0 0 -2.92 1.95",
        "M3.69 8.56a9 9 0 0 0 -.69 3.44",
        "M3.69 15.44a9 9 0 0 0 1.95 2.92",
        "M8.56 20.31a9 9 0 0 0 3.44 .69",
        "M15.44 20.31a9 9 0 0 0 2.92 -1.95",
        "M20.31 15.44a9 9 0 0 0 .69 -3.44",
        "M20.31 8.56a9 9 0 0 0 -1.95 -2.92",
        "M15.44 3.69a9 9 0 0 0 -3.44 -.69",
    ),
    "clock-hour-3": ("M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0", "M12 12h3.5", "M12 7v5"),
    "circle-check": ("M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0", "M9 12l2 2l4 -4"),
    "settings": (
        "M10.325 4.317c.426 -1.756 2.924 -1.756 3.35 0a1.724 1.724 0 0 0 2.573 1.066"
        "c1.543 -.94 3.31 .826 2.37 2.37a1.724 1.724 0 0 0 1.065 2.572"
        "c1.756 .426 1.756 2.924 0 3.35a1.724 1.724 0 0 0 -1.066 2.573"
        "c.94 1.543 -.826 3.31 -2.37 2.37a1.724 1.724 0 0 0 -2.572 1.065"
        "c-.426 1.756 -2.924 1.756 -3.35 0a1.724 1.724 0 0 0 -2.573 -1.066"
        "c-1.543 .94 -3.31 -.826 -2.37 -2.37a1.724 1.724 0 0 0 -1.065 -2.572"
        "c-1.756 -.426 -1.756 -2.924 0 -3.35a1.724 1.724 0 0 0 1.066 -2.573"
        "c-.94 -1.543 .826 -3.31 2.37 -2.37c1 .608 2.296 .07 2.572 -1.065",
        "M9 12a3 3 0 1 0 6 0a3 3 0 0 0 -6 0",
    ),
    "help-circle": (
        "M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0",
        "M12 16v.01",
        "M12 13a2 2 0 0 0 .914 -3.782a1.98 1.98 0 0 0 -2.414 .483",
    ),
    "info-circle": (
        "M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0",
        "M12 9h.01",
        "M11 12h1v4h1",
    ),
}


def tabler_pixmap(icon_id: str, size: int | QSize, color: QColor, *, dpr: float = 1.0) -> QPixmap:
    """Render one bundled outline icon at the requested logical size."""

    paths = _ICON_PATHS.get(icon_id)
    if paths is None:
        raise KeyError(f"unknown Tabler icon: {icon_id}")
    logical = QSize(size, size) if isinstance(size, int) else QSize(size)
    if logical.width() < 1 or logical.height() < 1:
        raise ValueError("icon size must be positive")
    ratio = max(1.0, float(dpr))
    pixels = QSize(max(1, round(logical.width() * ratio)), max(1, round(logical.height() * ratio)))
    path_markup = "".join(f'<path d="{escape(path)}"/>' for path in paths)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color.name()}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{path_markup}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise ValueError(f"invalid bundled Tabler icon: {icon_id}")
    pixmap = QPixmap(pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def tabler_icon(widget: QWidget, icon_id: str, size: int, *, semantic: str = "navigation") -> QIcon:
    """Build a QIcon whose modes follow the widget's current palette."""

    palette = widget.palette()
    dpr = widget.devicePixelRatioF()
    if semantic == "success":
        normal = active = palette.color(QPalette.ColorRole.LinkVisited)
    elif semantic == "accent":
        normal = active = palette.color(QPalette.ColorRole.Link)
    elif semantic == "navigation":
        normal = palette.color(QPalette.ColorRole.Text)
        active = palette.color(QPalette.ColorRole.Link)
    else:
        raise ValueError(f"unknown icon semantic: {semantic}")
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    icon = QIcon()
    icon.addPixmap(tabler_pixmap(icon_id, size, normal, dpr=dpr), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(tabler_pixmap(icon_id, size, active, dpr=dpr), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(tabler_pixmap(icon_id, size, active, dpr=dpr), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(tabler_pixmap(icon_id, size, active, dpr=dpr), QIcon.Mode.Active, QIcon.State.On)
    icon.addPixmap(tabler_pixmap(icon_id, size, active, dpr=dpr), QIcon.Mode.Selected, QIcon.State.On)
    icon.addPixmap(tabler_pixmap(icon_id, size, disabled, dpr=dpr), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


__all__ = ["tabler_icon", "tabler_pixmap"]
