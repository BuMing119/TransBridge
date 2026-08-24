"""Typed UI preferences stored by the unified :mod:`ConfigRepository`.

The repository remains the only INI owner.  This module only validates the
small public UI schema and turns write failures into an explicit result so a
presenter never claims that an uncommitted preference was saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .repository import ConfigRepository

UI_CONFIG_SECTION = "ui"
GUIDANCE_MODE_KEY = "guidance_mode"
THEME_MODE_KEY = "theme_mode"
THEME_ID_KEY = "theme_id"
LOCALE_KEY = "locale"

DEFAULT_THEME_ID = "transbridge.default"
DEFAULT_LOCALE = "zh-CN"
_THEME_ID_PATTERN = re.compile(r"(?=.{1,128}$)^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class GuidanceMode(StrEnum):
    """Supported explanation-density preferences."""

    AUTO = "auto"
    GUIDED = "guided"
    COMPACT = "compact"

    @classmethod
    def parse(cls, raw: str | None) -> tuple[GuidanceMode, bool]:
        value = (raw or "").strip().casefold()
        try:
            return cls(value), False
        except ValueError:
            return cls.AUTO, raw is not None


class ThemeMode(StrEnum):
    """Supported application theme preferences."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @classmethod
    def parse(cls, raw: str | None) -> tuple[ThemeMode, bool]:
        value = (raw or "").strip().casefold()
        if not value:
            return cls.SYSTEM, False
        try:
            return cls(value), False
        except ValueError:
            return cls.SYSTEM, True


@dataclass(frozen=True, slots=True)
class UiPreferenceSnapshot:
    guidance_mode: GuidanceMode
    config_revision: int
    used_fallback: bool = False
    diagnostic_code: str | None = None
    invalid_value: str | None = None
    theme_mode: ThemeMode = ThemeMode.SYSTEM
    theme_id: str = DEFAULT_THEME_ID
    locale: str = DEFAULT_LOCALE
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UiPreferenceSaveResult:
    requested_mode: GuidanceMode
    saved: bool
    snapshot: UiPreferenceSnapshot | None = None
    diagnostic_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.saved != (self.snapshot is not None):
            raise ValueError("saved result must contain exactly one committed snapshot")
        if self.saved and self.diagnostic_code is not None:
            raise ValueError("saved result cannot contain a failure diagnostic")
        if not self.saved and not self.diagnostic_code:
            raise ValueError("failed result requires a diagnostic code")


@dataclass(frozen=True, slots=True)
class UiFoundationPreferenceSaveResult:
    """Result of atomically persisting a theme or locale preference."""

    saved: bool
    snapshot: UiPreferenceSnapshot | None = None
    diagnostic_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.saved != (self.snapshot is not None):
            raise ValueError("saved result must contain exactly one committed snapshot")
        if self.saved and self.diagnostic_code is not None:
            raise ValueError("saved result cannot contain a failure diagnostic")
        if not self.saved and not self.diagnostic_code:
            raise ValueError("failed result requires a diagnostic code")


class UiPreferenceRepository:
    """Typed adapter over the versioned, atomic configuration repository."""

    def __init__(self, repository: ConfigRepository) -> None:
        self._repository = repository

    def load(self) -> UiPreferenceSnapshot:
        snapshot = self._repository.load()
        return self._snapshot_from_config(snapshot)

    @staticmethod
    def _snapshot_from_config(snapshot) -> UiPreferenceSnapshot:
        raw = snapshot.value(UI_CONFIG_SECTION, GUIDANCE_MODE_KEY)
        mode, used_fallback = GuidanceMode.parse(raw)
        raw_theme_mode = snapshot.value(UI_CONFIG_SECTION, THEME_MODE_KEY)
        theme_mode, theme_mode_fallback = ThemeMode.parse(raw_theme_mode)
        raw_theme_id = snapshot.value(UI_CONFIG_SECTION, THEME_ID_KEY)
        theme_id = (raw_theme_id or DEFAULT_THEME_ID).strip().casefold()
        theme_id_fallback = not bool(_THEME_ID_PATTERN.fullmatch(theme_id))
        if theme_id_fallback:
            theme_id = DEFAULT_THEME_ID
        raw_locale = snapshot.value(UI_CONFIG_SECTION, LOCALE_KEY)
        locale = (raw_locale or DEFAULT_LOCALE).strip()
        locale_fallback = not bool(_LOCALE_PATTERN.fullmatch(locale))
        if locale_fallback:
            locale = DEFAULT_LOCALE
        diagnostics = tuple(
            code
            for active, code in (
                (used_fallback, "ui_guidance_mode_invalid"),
                (theme_mode_fallback, "ui_theme_mode_invalid"),
                (theme_id_fallback, "ui_theme_id_invalid"),
                (locale_fallback, "ui_locale_invalid"),
            )
            if active
        )
        return UiPreferenceSnapshot(
            guidance_mode=mode,
            config_revision=snapshot.revision,
            used_fallback=bool(diagnostics),
            diagnostic_code=diagnostics[0] if diagnostics else None,
            invalid_value=raw if used_fallback else None,
            theme_mode=theme_mode,
            theme_id=theme_id,
            locale=locale,
            diagnostics=diagnostics,
        )

    def save_guidance_mode(self, mode: GuidanceMode) -> UiPreferenceSaveResult:
        if not isinstance(mode, GuidanceMode):
            raise TypeError("mode must be a GuidanceMode")
        try:
            snapshot = self._repository.update_sections({UI_CONFIG_SECTION: {GUIDANCE_MODE_KEY: mode.value}})
        except Exception as exc:
            code = getattr(exc, "code", "ui_guidance_mode_write_failed")
            return UiPreferenceSaveResult(
                requested_mode=mode,
                saved=False,
                diagnostic_code=str(code),
                message=str(exc),
            )
        return UiPreferenceSaveResult(
            requested_mode=mode,
            saved=True,
            snapshot=self._snapshot_from_config(snapshot),
        )

    def save_theme_preference(self, mode: ThemeMode, theme_id: str) -> UiFoundationPreferenceSaveResult:
        if not isinstance(mode, ThemeMode):
            raise TypeError("mode must be a ThemeMode")
        if not isinstance(theme_id, str):
            raise TypeError("theme_id must be a string")
        normalized_theme_id = theme_id.strip().casefold()
        if not _THEME_ID_PATTERN.fullmatch(normalized_theme_id):
            return UiFoundationPreferenceSaveResult(
                saved=False,
                diagnostic_code="ui_theme_id_invalid",
                message="theme ID does not match the supported identifier format",
            )
        return self._save_foundation_values(
            {THEME_MODE_KEY: mode.value, THEME_ID_KEY: normalized_theme_id},
            failure_code="ui_theme_preference_write_failed",
        )

    def save_locale(self, locale: str) -> UiFoundationPreferenceSaveResult:
        if not isinstance(locale, str):
            raise TypeError("locale must be a string")
        normalized_locale = locale.strip()
        if not _LOCALE_PATTERN.fullmatch(normalized_locale):
            return UiFoundationPreferenceSaveResult(
                saved=False,
                diagnostic_code="ui_locale_invalid",
                message="locale does not match the supported identifier format",
            )
        return self._save_foundation_values(
            {LOCALE_KEY: normalized_locale},
            failure_code="ui_locale_write_failed",
        )

    def _save_foundation_values(
        self,
        values: dict[str, str],
        *,
        failure_code: str,
    ) -> UiFoundationPreferenceSaveResult:
        try:
            snapshot = self._repository.update_sections({UI_CONFIG_SECTION: values})
        except Exception as exc:
            code = getattr(exc, "code", failure_code)
            return UiFoundationPreferenceSaveResult(
                saved=False,
                diagnostic_code=str(code),
                message=str(exc),
            )
        return UiFoundationPreferenceSaveResult(saved=True, snapshot=self._snapshot_from_config(snapshot))


__all__ = [
    "DEFAULT_LOCALE",
    "DEFAULT_THEME_ID",
    "GUIDANCE_MODE_KEY",
    "LOCALE_KEY",
    "THEME_ID_KEY",
    "THEME_MODE_KEY",
    "UI_CONFIG_SECTION",
    "GuidanceMode",
    "ThemeMode",
    "UiFoundationPreferenceSaveResult",
    "UiPreferenceRepository",
    "UiPreferenceSaveResult",
    "UiPreferenceSnapshot",
]
