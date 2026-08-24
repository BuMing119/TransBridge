"""Theme-aware item painting for the Workbench translation table."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QPainter, QPalette
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from transbridge.converter.translation_entry import (
    STAGE_HIDDEN,
    STAGE_LOCKED,
    STAGE_TRANSLATED,
    TranslationEntry,
)
from transbridge.ui.foundation.adapters import DomainBrushes, ThemeView

from ._theme_support import readable_user_color
from .translation_table_columns import COL_CONTEXT, COL_KEY, COL_MARK, COL_TRANSLATION

if TYPE_CHECKING:
    from .translation_table import TranslationTable


class TranslationThemeDelegate(QStyledItemDelegate):
    """Paint theme state from entry identity without mutating table items."""

    def __init__(self, table: TranslationTable, theme_view: ThemeView | None) -> None:
        super().__init__(table)
        self._table = table
        self._domain: DomainBrushes | None = None
        self._row_backgrounds: dict[str, QBrush] = {}
        self._stage_backgrounds: dict[str, QBrush] = {}
        self._stage_foregrounds: dict[str, QBrush] = {}
        self._text_palettes: dict[tuple[str, str], QPalette] = {}
        self._domain_factory = DomainBrushes if theme_view is None else theme_view.domain_brushes
        self._revision = 0
        if theme_view is not None:
            self.apply_theme(theme_view.snapshot())
            theme_view.subscribe(table, self.apply_theme)

    @property
    def revision(self) -> int:
        return self._revision

    def apply_theme(self, snapshot) -> None:
        try:
            domain = self._domain_factory(snapshot)
        except Exception:  # noqa: BLE001 - retain last-good brushes on adapter failure
            return
        base_palette = QPalette(self._table.palette())
        row_backgrounds = {
            "hidden": domain.translation("hidden").background,
            "locked": domain.translation("locked").background,
        }
        stage_backgrounds = {style.key: domain.stage(style.key).background for style in snapshot.tokens.domain.stages}
        stage_foregrounds = {style.key: domain.stage(style.key).foreground for style in snapshot.tokens.domain.stages}
        stage_backgrounds[str(STAGE_TRANSLATED)] = domain.stage("0").background
        stage_foregrounds[str(STAGE_TRANSLATED)] = domain.stage("0").foreground
        text_palettes = {
            ("stage", style.key): self._text_palette(base_palette, domain.stage(style.key).foreground)
            for style in snapshot.tokens.domain.stages
        }
        text_palettes.update({
            ("translation", state): self._text_palette(base_palette, domain.translation(state).foreground)
            for state in ("source", "translated")
        })
        self._domain = domain
        self._row_backgrounds = row_backgrounds
        self._stage_backgrounds = stage_backgrounds
        self._stage_foregrounds = stage_foregrounds
        self._text_palettes = text_palettes
        self._revision = snapshot.revision
        self._table.viewport().update()

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt override
        super().initStyleOption(option, index)
        domain = self._domain
        entry = index.data(Qt.ItemDataRole.UserRole)
        if domain is None or not isinstance(entry, TranslationEntry):
            return
        background = self._background(entry, index.column())
        if background is not None:
            option.backgroundBrush = background
        text_palette = None
        if index.column() in (COL_KEY, COL_CONTEXT):
            text_palette = self._text_palettes.get(("stage", str(entry.stage)))
        elif index.column() == COL_TRANSLATION:
            state = "translated" if entry.translation else "source"
            text_palette = self._text_palettes[("translation", state)]
        elif index.column() == COL_MARK:
            labels = self._table._entry_labels.get(entry.id, set()) if entry.id else set()
            if labels:
                raw = self._table._label_library.get(next(iter(labels)), {}).get("color")
                foreground = readable_user_color(raw, option.palette)
                text_palette = self._text_palette(option.palette, QBrush(foreground))
        if text_palette is not None:
            option.palette = text_palette

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if index.column() != COL_CONTEXT:
            super().paint(painter, option, index)
            return
        entry = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, TranslationEntry):
            super().paint(painter, option, index)
            return

        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        styled.backgroundBrush = QBrush()
        super().paint(painter, styled, index)

        background = self._stage_backgrounds.get(
            str(entry.stage), option.palette.brush(QPalette.ColorRole.AlternateBase)
        )
        foreground = self._stage_foregrounds.get(str(entry.stage), option.palette.brush(QPalette.ColorRole.Text))
        available = option.rect.adjusted(8, 5, -8, -5)
        width = min(available.width(), option.fontMetrics.horizontalAdvance(text) + 18)
        pill = available
        pill.setWidth(max(0, width))
        text = option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, pill.width() - 12))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill, 9, 9)
        painter.setPen(foreground.color())
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _background(self, entry: TranslationEntry, column: int) -> QBrush | None:
        if column == COL_CONTEXT:
            return self._stage_backgrounds.get(str(entry.stage))
        if entry.stage == STAGE_HIDDEN:
            return self._row_backgrounds["hidden"]
        if entry.stage == STAGE_LOCKED:
            return self._row_backgrounds["locked"]
        return None

    @staticmethod
    def _text_palette(base: QPalette, foreground: QBrush) -> QPalette:
        palette = QPalette(base)
        palette.setBrush(QPalette.ColorRole.Text, foreground)
        return palette


__all__ = ["TranslationThemeDelegate"]
