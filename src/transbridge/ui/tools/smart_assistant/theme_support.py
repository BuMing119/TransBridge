"""Feature-local presentation adapter for Smart Assistant theme consumers."""

from __future__ import annotations

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.infra.markdown_renderer import MarkdownRenderTheme
from transbridge.ui.foundation.adapters import DomainBrushes, RichTextThemeAdapter
from transbridge.ui.foundation.qt_palette import qcolor
from transbridge.ui.foundation.theme_service import ThemeSnapshot

CARD_STRUCTURE_STYLE = (
    '*[tbSurface="card"] { border: 1px solid palette(mid); border-radius: 10px; padding: 8px; '
    "background-color: palette(base); }"
)
BUTTON_STRUCTURE_STYLE = (
    "QPushButton { border: 1px solid palette(mid); border-radius: 6px; padding: 4px 12px; "
    "background-color: palette(button); color: palette(button-text); }"
)
CHIP_STRUCTURE_STYLE = (
    "QPushButton { border: 1px solid palette(mid); border-radius: 12px; padding: 3px 10px; font-size: 11px; "
    "background-color: palette(button); color: palette(button-text); }"
)
INPUT_STRUCTURE_STYLE = (
    "QTextEdit { border: 1px solid palette(mid); border-radius: 8px; padding: 6px 10px; "
    "background-color: palette(base); color: palette(text); }"
)
PANEL_STRUCTURE_STYLE = "QDockWidget#SmartAssistantPanel { border: none; background-color: palette(base); }"
HEADER_STRUCTURE_STYLE = (
    "QFrame#smartAssistantHeader { border: 2px solid palette(text); border-bottom: 2px solid palette(text); "
    "border-top-left-radius: 12px; border-top-right-radius: 12px; background-color: palette(base); }"
)
BODY_STRUCTURE_STYLE = (
    "QFrame#smartAssistantBody { border-left: 2px solid palette(text); border-right: 2px solid palette(text); "
    "border-bottom: 2px solid palette(text); border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; "
    "background-color: palette(base); }"
)
TRANSPARENT_STRUCTURE_STYLE = "background: transparent; border: none;"


def _build_markdown_theme(
    *,
    fingerprint: str,
    foreground: str,
    surface: str,
    inline_surface: str,
    border: str,
    selection: str,
    selection_text: str,
    link: str,
) -> MarkdownRenderTheme:
    return MarkdownRenderTheme(
        fingerprint=fingerprint,
        stylesheet=(
            f"color: {foreground}; background-color: {surface}; "
            f"selection-color: {selection_text}; selection-background-color: {selection};"
        ),
        inline_code_style=(
            f"background-color:{inline_surface}; color:{foreground}; padding:1px 4px; "
            "border-radius:3px; font-family:Consolas,monospace; font-size:12px;"
        ),
        code_block_stylesheet=(
            "QTextEdit {"
            f"background-color: {inline_surface}; color: {foreground}; border: 1px solid {border};"
            "border-radius: 6px; padding: 8px;"
            "}"
        ),
        horizontal_rule_stylesheet=f"QFrame {{ border-color: {border}; margin: 8px 0; }}",
        link_color=link,
    )


def _css_color(value) -> str:
    # Markdown body text must remain opaque enough to read. QColor.name()
    # deliberately emits unambiguous CSS #RRGGBB instead of theme #RRGGBBAA.
    return qcolor(value).name()


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

    def markdown_theme(self, *, alternate: bool = False):
        if self._snapshot is not None:
            if not alternate:
                return self._rich_text.theme(self._snapshot)
            semantic = self._snapshot.tokens.semantic
            return _build_markdown_theme(
                fingerprint=f"{self._snapshot.fingerprint}:alternate",
                foreground=_css_color(semantic.text_primary),
                surface=_css_color(semantic.surface_alt),
                inline_surface=_css_color(semantic.surface),
                border=_css_color(semantic.border),
                selection=_css_color(semantic.selection_background),
                selection_text=_css_color(semantic.selection_text),
                link=_css_color(semantic.link),
            )
        app = QApplication.instance()
        if app is None:
            return None
        palette = app.palette()
        foreground = palette.color(QPalette.ColorRole.Text).name()
        base_surface = palette.color(QPalette.ColorRole.Base).name()
        alternate_surface = palette.color(QPalette.ColorRole.AlternateBase).name()
        surface = alternate_surface if alternate else base_surface
        inline_surface = base_surface if alternate else alternate_surface
        border = palette.color(QPalette.ColorRole.Mid).name()
        selection = palette.color(QPalette.ColorRole.Highlight).name()
        selection_text = palette.color(QPalette.ColorRole.HighlightedText).name()
        link = palette.color(QPalette.ColorRole.Link).name()
        fingerprint = f"qt:{foreground}:{base_surface}:{alternate_surface}:{border}:{link}"
        return _build_markdown_theme(
            fingerprint=f"{fingerprint}:{'alternate' if alternate else 'surface'}",
            foreground=foreground,
            surface=surface,
            inline_surface=inline_surface,
            border=border,
            selection=selection,
            selection_text=selection_text,
            link=link,
        )

    def apply_surface(
        self,
        widget: QWidget,
        *,
        alternate: bool = False,
        secondary_text: bool = False,
    ) -> None:
        """Apply a neutral surface without turning readable body text into a status colour."""
        widget.setProperty("tbSurfaceTone", "alternate" if alternate else "surface")
        snapshot = self._snapshot
        if snapshot is not None:
            semantic = snapshot.tokens.semantic
            foreground = semantic.text_secondary if secondary_text else semantic.text_primary
            background = semantic.surface_alt if alternate else semantic.surface
            self._apply_palette(widget, foreground, background)
            return
        palette = widget.palette()
        foreground_role = QPalette.ColorRole.PlaceholderText if secondary_text else QPalette.ColorRole.Text
        background_role = QPalette.ColorRole.AlternateBase if alternate else QPalette.ColorRole.Base
        self._apply_palette(
            widget,
            palette.color(foreground_role),
            palette.color(background_role),
            colors_are_qt=True,
        )

    def apply_accent(self, widget: QWidget) -> None:
        """Apply the theme focus colour as an accessible primary action surface."""
        widget.setProperty("tbSemanticState", "accent")
        snapshot = self._snapshot
        if snapshot is not None:
            semantic = snapshot.tokens.semantic
            self._apply_palette(widget, semantic.selection_text, semantic.focus)
            return
        palette = widget.palette()
        self._apply_palette(
            widget,
            palette.color(QPalette.ColorRole.HighlightedText),
            palette.color(QPalette.ColorRole.Highlight),
            colors_are_qt=True,
        )

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
    "BODY_STRUCTURE_STYLE",
    "BUTTON_STRUCTURE_STYLE",
    "CARD_STRUCTURE_STYLE",
    "CHIP_STRUCTURE_STYLE",
    "HEADER_STRUCTURE_STYLE",
    "INPUT_STRUCTURE_STYLE",
    "PANEL_STRUCTURE_STYLE",
    "SmartAssistantTheme",
    "TRANSPARENT_STRUCTURE_STYLE",
]
