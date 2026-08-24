"""Typed UI preferences stored by the unified :mod:`ConfigRepository`.

The repository remains the only INI owner.  This module only validates the
small public UI schema and turns write failures into an explicit result so a
presenter never claims that an uncommitted preference was saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .repository import ConfigRepository

UI_CONFIG_SECTION = "ui"
GUIDANCE_MODE_KEY = "guidance_mode"


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


@dataclass(frozen=True, slots=True)
class UiPreferenceSnapshot:
    guidance_mode: GuidanceMode
    config_revision: int
    used_fallback: bool = False
    diagnostic_code: str | None = None
    invalid_value: str | None = None


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


class UiPreferenceRepository:
    """Typed adapter over the versioned, atomic configuration repository."""

    def __init__(self, repository: ConfigRepository) -> None:
        self._repository = repository

    def load(self) -> UiPreferenceSnapshot:
        snapshot = self._repository.load()
        raw = snapshot.value(UI_CONFIG_SECTION, GUIDANCE_MODE_KEY)
        mode, used_fallback = GuidanceMode.parse(raw)
        return UiPreferenceSnapshot(
            guidance_mode=mode,
            config_revision=snapshot.revision,
            used_fallback=used_fallback,
            diagnostic_code="ui_guidance_mode_invalid" if used_fallback else None,
            invalid_value=raw if used_fallback else None,
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
            snapshot=UiPreferenceSnapshot(mode, snapshot.revision),
        )


__all__ = [
    "GUIDANCE_MODE_KEY",
    "UI_CONFIG_SECTION",
    "GuidanceMode",
    "UiPreferenceRepository",
    "UiPreferenceSaveResult",
    "UiPreferenceSnapshot",
]
