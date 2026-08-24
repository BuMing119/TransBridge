"""Narrow palette/domain helpers for AI Translator top-level views."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QWidget

from transbridge.ui.foundation.adapters import DomainBrush, DomainBrushes, ThemeSubscription, ThemeView


class AiThemeBinding:
    """One guarded ThemeView subscription owned by one top-level AI surface."""

    def __init__(
        self,
        owner: QWidget,
        theme_view: ThemeView | None,
        callback: Callable[[AiThemeBinding], None] | None = None,
    ) -> None:
        self.revision = 0
        self.domain: DomainBrushes | None = None
        self._callback = callback
        self._subscription: ThemeSubscription | None = None
        if theme_view is not None:
            self._apply(theme_view.snapshot())
            self._subscription = theme_view.subscribe(owner, self._apply)

    def _apply(self, snapshot) -> None:
        try:
            domain = DomainBrushes(snapshot)
        except Exception:  # noqa: BLE001 - retain the last-good compiled brushes
            return
        self.domain = domain
        self.revision = snapshot.revision
        if self._callback is not None:
            self._callback(self)

    def task(self, key: str) -> DomainBrush | None:
        return None if self.domain is None else self.domain.task(key)

    def report(self, key: str) -> DomainBrush | None:
        return None if self.domain is None else self.domain.report(key)

    def close(self) -> None:
        if self._subscription is not None:
            self._subscription.close()
            self._subscription = None


def set_widget_brush(widget: QWidget, brush: DomainBrush | None) -> None:
    if brush is None:
        return
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.WindowText, brush.foreground.color())
    palette.setColor(QPalette.ColorRole.Base, brush.background.color())
    widget.setPalette(palette)
    widget.update()


__all__ = ["AiThemeBinding", "set_widget_brush"]
