"""Workbench-only helpers for readable user colours and widget palettes."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QWidget


def readable_user_color(
    raw_color: object,
    palette: QPalette,
    *,
    background_role: QPalette.ColorRole = QPalette.ColorRole.Base,
    foreground_role: QPalette.ColorRole = QPalette.ColorRole.Text,
) -> QColor:
    """Keep valid user colour data only when it remains legible on the current theme."""
    candidate = QColor(str(raw_color)) if isinstance(raw_color, str) else QColor()
    fallback = palette.color(foreground_role)
    if not candidate.isValid():
        return fallback
    background = palette.color(background_role)
    return candidate if _contrast(candidate, background) >= 4.5 else fallback


def set_widget_foreground(
    widget: QWidget,
    color: QColor,
    *,
    role: QPalette.ColorRole = QPalette.ColorRole.WindowText,
) -> None:
    palette = QPalette(widget.palette())
    palette.setColor(role, color)
    widget.setPalette(palette)


def _contrast(foreground: QColor, background: QColor) -> float:
    foreground_rgb = _composite(foreground, background)
    foreground_luminance = _luminance(foreground_rgb)
    background_luminance = _luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _composite(foreground: QColor, background: QColor) -> QColor:
    alpha = foreground.alphaF()
    return QColor.fromRgbF(
        foreground.redF() * alpha + background.redF() * (1 - alpha),
        foreground.greenF() * alpha + background.greenF() * (1 - alpha),
        foreground.blueF() * alpha + background.blueF() * (1 - alpha),
    )


def _luminance(color: QColor) -> float:
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = tuple(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


__all__ = ["readable_user_color", "set_widget_foreground"]
