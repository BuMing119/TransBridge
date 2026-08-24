"""Compile validated semantic theme tokens into a Qt application palette."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

from .model import RgbaColor, ThemeDefinition


def qcolor(color: RgbaColor) -> QColor:
    return QColor(color.red, color.green, color.blue, color.alpha)


def _blend(foreground: RgbaColor, background: RgbaColor, amount: float) -> RgbaColor:
    inverse = 1.0 - amount
    return RgbaColor(
        round(foreground.red * amount + background.red * inverse),
        round(foreground.green * amount + background.green * inverse),
        round(foreground.blue * amount + background.blue * inverse),
    )


def compile_palette(definition: ThemeDefinition) -> QPalette:
    """Compile all standard widget roles once, outside paint hot paths."""

    token = definition.tokens.semantic
    palette = QPalette()
    subtle_border = _blend(token.border, token.surface, 0.68)
    hover_surface = _blend(token.focus, token.surface, 0.08)
    alternate_surface = _blend(token.surface_alt, token.surface, 0.34)
    header_surface = _blend(token.surface_alt, token.surface, 0.62)
    selected_surface = _blend(token.focus, token.surface, 0.11)
    roles = {
        QPalette.ColorRole.Window: token.window,
        QPalette.ColorRole.WindowText: token.text_primary,
        QPalette.ColorRole.Base: token.surface,
        QPalette.ColorRole.AlternateBase: alternate_surface,
        QPalette.ColorRole.Light: hover_surface,
        QPalette.ColorRole.Midlight: header_surface,
        QPalette.ColorRole.Mid: subtle_border,
        QPalette.ColorRole.Dark: token.warning,
        QPalette.ColorRole.Shadow: token.text_secondary,
        QPalette.ColorRole.ToolTipBase: token.surface,
        QPalette.ColorRole.ToolTipText: token.text_primary,
        QPalette.ColorRole.Text: token.text_primary,
        QPalette.ColorRole.Button: token.surface,
        QPalette.ColorRole.ButtonText: token.text_primary,
        QPalette.ColorRole.BrightText: token.error,
        QPalette.ColorRole.Link: token.focus,
        QPalette.ColorRole.LinkVisited: token.success,
        QPalette.ColorRole.Highlight: selected_surface,
        QPalette.ColorRole.HighlightedText: token.text_primary,
        QPalette.ColorRole.PlaceholderText: token.text_secondary,
    }
    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        roles[accent_role] = token.focus
    for role, color in roles.items():
        palette.setColor(role, qcolor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, qcolor(token.disabled_text))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, qcolor(token.disabled_text))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, qcolor(token.disabled_text))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, qcolor(token.disabled_surface))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, qcolor(token.disabled_surface))
    return palette


__all__ = ["compile_palette", "qcolor"]
