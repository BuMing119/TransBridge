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
    "home": (
        "M19 8.71l-5.333 -4.148a2.666 2.666 0 0 0 -3.274 0l-5.334 4.148"
        "a2.665 2.665 0 0 0 -1.029 2.105v7.2a2 2 0 0 0 2 2h12c1.1 0 2 -.9 2 -2"
        "v-7.2c0 -.823 -.38 -1.6 -1.03 -2.105",
        "M16 21c0 -2.21 -1.79 -4 -4 -4s-4 1.79 -4 4",
    ),
    "folder": ("M5 4h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-11a2 2 0 0 1 2 -2",),
    "package": (
        "M12 3l8 4.5v9l-8 4.5l-8 -4.5v-9l8 -4.5",
        "M12 12l8 -4.5",
        "M12 12v9",
        "M12 12l-8 -4.5",
        "M16 5.25l-8 4.5",
    ),
    "arrow-left": ("M5 12l14 0", "M5 12l6 6", "M5 12l6 -6"),
    "chevron-right": ("M9 6l6 6l-6 6",),
    "alert-triangle": (
        "M12 9v4",
        "M12 17v.01",
        "M5.07 19h13.86a2 2 0 0 0 1.74 -3l-6.93 -12a2 2 0 0 0 -3.48 0l-6.93 12a2 2 0 0 0 1.74 3",
    ),
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
    "minus": ("M5 12l14 0",),
    "search": (
        "M10 4a6 6 0 1 0 0 12a6 6 0 0 0 0 -12",
        "M14.5 14.5l5.5 5.5",
    ),
    "x": ("M18 6l-12 12", "M6 6l12 12"),
    "dots": ("M5 12v.01", "M12 12v.01", "M19 12v.01"),
    "chevron-left": ("M15 6l-6 6l6 6",),
    "sparkles": (
        "M12 3l1.2 3.3a4 4 0 0 0 2.5 2.5l3.3 1.2l-3.3 1.2"
        "a4 4 0 0 0 -2.5 2.5l-1.2 3.3l-1.2 -3.3a4 4 0 0 0 -2.5 -2.5"
        "l-3.3 -1.2l3.3 -1.2a4 4 0 0 0 2.5 -2.5l1.2 -3.3",
        "M5 3v4",
        "M3 5h4",
        "M19 17v4",
        "M17 19h4",
    ),
    "paperclip": ("M15 7l-6.5 6.5a2.12 2.12 0 0 0 3 3l7 -7a4.24 4.24 0 0 0 -6 -6l-7 7a6.36 6.36 0 0 0 9 9l6.5 -6.5",),
    "send": (
        "M10 14l11 -11",
        "M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5",
    ),
    "shield-check": (
        "M12 3l7 4v5a8 8 0 0 1 -7 8a8 8 0 0 1 -7 -8v-5l7 -4",
        "M9 12l2 2l4 -4",
    ),
    "book": (
        "M4 19.5a2.5 2.5 0 0 1 2.5 -2.5h13.5",
        "M6.5 2h13.5v20h-13.5a2.5 2.5 0 0 1 -2.5 -2.5v-15a2.5 2.5 0 0 1 2.5 -2.5",
    ),
    "trash": (
        "M4 7h16",
        "M10 11v6",
        "M14 11v6",
        "M5 7l1 14h12l1 -14",
        "M9 7v-3h6v3",
    ),
    "user": (
        "M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0",
        "M6 21v-2a6 6 0 0 1 12 0v2",
    ),
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
    elif semantic == "on-accent":
        normal = active = palette.color(QPalette.ColorRole.ButtonText)
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
