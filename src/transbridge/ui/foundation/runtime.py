"""GUI composition object that owns UI-only foundation services."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from PyQt6.QtWidgets import QApplication

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository

from .accessibility import UnavailableAccessibilityHintsSource
from .adapters import DomainBrushes
from .builtins import create_builtin_registry
from .locale_service import LocaleService
from .registry import ThemeRegistry
from .theme_service import ThemeApplyResult, ThemeApplyStatus, ThemePreference, ThemeService


@dataclass(slots=True)
class GuiFoundation:
    theme: ThemeService
    registry: ThemeRegistry
    config: UiPreferenceRepository
    initial_theme_result: ThemeApplyResult
    locale: LocaleService | None = None
    accessibility: UnavailableAccessibilityHintsSource | None = None
    domain_brush_cache: OrderedDict[str, DomainBrushes] = field(default_factory=OrderedDict)

    @classmethod
    def create(
        cls,
        application: QApplication,
        preferences: UiPreferenceRepository,
    ) -> GuiFoundation:
        registry = create_builtin_registry()
        theme = ThemeService(application, registry, preferences)
        initial = theme.start()
        if initial.status is ThemeApplyStatus.FAILED and initial.snapshot is None:
            initial = theme.set_preference(
                ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID),
                persist=False,
            )
        locale = LocaleService(preferences)
        locale.start()
        return cls(
            theme=theme,
            registry=registry,
            config=preferences,
            initial_theme_result=initial,
            locale=locale,
            accessibility=UnavailableAccessibilityHintsSource(),
        )

    def close(self) -> None:
        locale_close = getattr(self.locale, "close", None)
        if callable(locale_close):
            locale_close()
        self.domain_brush_cache.clear()
        self.theme.close()


__all__ = ["GuiFoundation"]
