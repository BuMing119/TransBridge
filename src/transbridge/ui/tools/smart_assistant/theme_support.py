"""Feature-local presentation adapter for Smart Assistant theme consumers."""

from __future__ import annotations

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QWidget

from transbridge.ui.foundation.adapters import DomainBrushes, RichTextThemeAdapter
from transbridge.ui.foundation.qt_palette import qcolor
from transbridge.ui.foundation.theme_service import ThemeSnapshot

CARD_STRUCTURE_STYLE = (
    '*[tbSurface="card"] { border-width: 1px; border-style: solid; border-radius: 10px; padding: 8px; }'
)
BUTTON_STRUCTURE_STYLE = (
    "QPushButton { border-width: 1px; border-style: solid; border-radius: 6px; padding: 4px 12px; }"
)
CHIP_STRUCTURE_STYLE = "QPushButton { border-radius: 12px; padding: 3px 10px; font-size: 11px; }"
INPUT_STRUCTURE_STYLE = "QTextEdit { border-width: 1px; border-style: solid; border-radius: 8px; padding: 6px 10px; }"
TRANSPARENT_STRUCTURE_STYLE = "background: transparent; border: none;"


class SmartAssistantTheme:
    """One mutable revision projection shared by a panel and its owned views."""

    def __init__(self, snapshot: ThemeSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self._domain = None if snapshot is None else DomainBrushes(snapshot)
        self._rich_text = RichTextThemeAdapter()

    @property
    def revision(self) -> int:
        return 0 if self._snapshot is None else self._snapshot.revision

    @property
    def fingerprint(self) -> str:
        return "qt-palette" if self._snapshot is None else self._snapshot.fingerprint

    def update(self, snapshot: ThemeSnapshot) -> None:
        if self._snapshot is not None and self._snapshot.fingerprint == snapshot.fingerprint:
            self._snapshot = snapshot
            return
        self._snapshot = snapshot
        self._domain = DomainBrushes(snapshot)

    def markdown_theme(self):
        return None if self._snapshot is None else self._rich_text.theme(self._snapshot)

    def apply_semantic(self, widget: QWidget, state: str, *, background: bool = False) -> None:
        widget.setProperty("tbSemanticState", state)
        snapshot = self._snapshot
        if snapshot is None:
            widget.update()
            return
        semantic = snapshot.tokens.semantic
        foreground = {
            "success": semantic.success,
            "warning": semantic.warning,
            "error": semantic.error,
            "info": semantic.info,
            "muted": semantic.text_secondary,
            "primary": semantic.focus,
        }.get(state, semantic.text_primary)
        surface = {
            "success": semantic.surface_alt,
            "warning": semantic.surface_alt,
            "error": semantic.surface_alt,
            "info": semantic.surface_alt,
            "muted": semantic.surface_alt,
            "primary": semantic.surface,
        }.get(state, semantic.surface)
        self._apply_palette(widget, foreground, surface if background else None)

    def apply_domain(self, widget: QWidget, category: str, key: str | int, *, background: bool = False) -> None:
        widget.setProperty("tbDomainState", f"{category}.{key}")
        if self._domain is None:
            widget.update()
            return
        method_name = {
            "stages": "stage",
            "labels": "label",
            "diff": "diff",
            "translation": "translation",
            "task": "task",
            "report": "report",
        }.get(category, category)
        method = getattr(self._domain, method_name)
        brushes = method(key)
        self._apply_palette(
            widget,
            brushes.foreground.color(),
            brushes.background.color() if background else None,
            colors_are_qt=True,
        )

    @staticmethod
    def mark_status(widget: QWidget, text: str, state: str) -> None:
        widget.setProperty("tbSemanticState", state)
        widget.setAccessibleName(text)
        widget.setAccessibleDescription(f"{text}；状态：{state}")

    @staticmethod
    def _apply_palette(
        widget: QWidget,
        foreground,
        background=None,
        *,
        colors_are_qt: bool = False,
    ) -> None:
        palette = QPalette(widget.palette())
        foreground_color = foreground if colors_are_qt else qcolor(foreground)
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(role, foreground_color)
        if background is not None:
            background_color = background if colors_are_qt else qcolor(background)
            for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
                palette.setColor(role, background_color)
            widget.setAutoFillBackground(True)
        widget.setPalette(palette)
        widget.update()


__all__ = [
    "BUTTON_STRUCTURE_STYLE",
    "CARD_STRUCTURE_STYLE",
    "CHIP_STRUCTURE_STYLE",
    "INPUT_STRUCTURE_STYLE",
    "SmartAssistantTheme",
    "TRANSPARENT_STRUCTURE_STYLE",
]
