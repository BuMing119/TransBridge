"""Qt-free accessibility and WCAG contrast helpers for UI Foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Protocol, runtime_checkable

_HEX_COLOR = re.compile(r"^#(?P<value>[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


class ContrastRole(StrEnum):
    TEXT = "text"
    LARGE_TEXT = "large_text"
    UI_COMPONENT = "ui_component"
    FOCUS_INDICATOR = "focus_indicator"

    @property
    def minimum_ratio(self) -> float:
        return 4.5 if self is ContrastRole.TEXT else 3.0


@dataclass(frozen=True, slots=True)
class RgbColor:
    red: int
    green: int
    blue: int
    alpha: int = 255

    def __post_init__(self) -> None:
        if any(not isinstance(channel, int) or not 0 <= channel <= 255 for channel in self.channels):
            raise ValueError("color channels must be integers from 0 through 255")

    @property
    def channels(self) -> tuple[int, int, int, int]:
        return self.red, self.green, self.blue, self.alpha


ColorInput = str | RgbColor | tuple[int, int, int] | tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ContrastPair:
    name: str
    foreground: ColorInput
    background: ColorInput
    role: ContrastRole = ContrastRole.TEXT
    exempt: bool = False
    exemption_reason: str = ""


@dataclass(frozen=True, slots=True)
class ContrastIssue:
    pair_name: str
    code: str
    message: str
    actual_ratio: float | None = None
    required_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class ContrastValidationResult:
    issues: tuple[ContrastIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class AccessibleStateCue:
    state_id: str
    visible_text: str = ""
    icon_id: str | None = None
    accessible_description: str = ""


@dataclass(frozen=True, slots=True)
class AccessibilityHintsSnapshot:
    available: bool
    reduce_motion: bool | None = None
    increase_contrast: bool | None = None


@runtime_checkable
class AccessibilityHintsSource(Protocol):
    def snapshot(self) -> AccessibilityHintsSnapshot: ...


class UnavailableAccessibilityHintsSource:
    """Qt 6.5-safe default used until a newer Qt adapter is installed."""

    @staticmethod
    def snapshot() -> AccessibilityHintsSnapshot:
        return AccessibilityHintsSnapshot(available=False)


@runtime_checkable
class AccessibleWidget(Protocol):
    def setAccessibleName(self, name: str) -> None: ...  # noqa: N802 - Qt-compatible protocol

    def setAccessibleDescription(self, description: str) -> None: ...  # noqa: N802 - Qt-compatible protocol


def parse_color(value: ColorInput | object) -> RgbColor:
    if isinstance(value, RgbColor):
        return value
    if isinstance(value, tuple):
        if len(value) not in {3, 4}:
            raise ValueError("color tuple must contain three or four channels")
        return RgbColor(*value) if len(value) == 4 else RgbColor(*value, 255)
    if not isinstance(value, str):
        channels = tuple(getattr(value, name, None) for name in ("red", "green", "blue", "alpha"))
        if all(isinstance(channel, int) for channel in channels):
            return RgbColor(*channels)
        raise TypeError("color must be a hex string, channel tuple, or RGBA value object")
    match = _HEX_COLOR.fullmatch(value)
    if match is None:
        raise ValueError("color must use #RGB, #RGBA, #RRGGBB, or #RRGGBBAA")
    raw = match.group("value")
    if len(raw) in {3, 4}:
        raw = "".join(channel * 2 for channel in raw)
    if len(raw) == 6:
        raw += "FF"
    return RgbColor(*(int(raw[index : index + 2], 16) for index in range(0, 8, 2)))


def contrast_ratio(foreground: ColorInput | object, background: ColorInput | object) -> float:
    """Return the unrounded WCAG contrast ratio after alpha compositing."""

    background_color = parse_color(background)
    if background_color.alpha != 255:
        raise ValueError("contrast background must be opaque")
    foreground_color = _composite(parse_color(foreground), background_color)
    lighter = max(_relative_luminance(foreground_color), _relative_luminance(background_color))
    darker = min(_relative_luminance(foreground_color), _relative_luminance(background_color))
    return (lighter + 0.05) / (darker + 0.05)


def validate_contrast_pairs(pairs: tuple[ContrastPair, ...] | list[ContrastPair]) -> ContrastValidationResult:
    issues: list[ContrastIssue] = []
    seen: set[str] = set()
    for pair in pairs:
        if not pair.name or pair.name in seen:
            issues.append(
                ContrastIssue(pair.name, "contrast_pair_duplicate", "contrast pair names must be non-empty and unique")
            )
            continue
        seen.add(pair.name)
        if pair.exempt:
            if not pair.exemption_reason.strip():
                issues.append(
                    ContrastIssue(pair.name, "contrast_exemption_reason_missing", "an exemption requires a reason")
                )
            continue
        try:
            ratio = contrast_ratio(pair.foreground, pair.background)
        except (TypeError, ValueError) as exc:
            issues.append(ContrastIssue(pair.name, "contrast_color_invalid", str(exc)))
            continue
        minimum = pair.role.minimum_ratio
        if ratio < minimum:
            issues.append(
                ContrastIssue(
                    pair.name,
                    "contrast_ratio_too_low",
                    f"contrast ratio {ratio:.4f} is below {minimum:.1f}",
                    actual_ratio=ratio,
                    required_ratio=minimum,
                )
            )
    return ContrastValidationResult(tuple(issues))


def validate_state_cue(cue: AccessibleStateCue) -> tuple[str, ...]:
    """Require state meaning in text, or in an icon with accessible text."""

    issues: list[str] = []
    if not cue.state_id.strip():
        issues.append("accessible_state_id_missing")
    has_text = bool(cue.visible_text.strip())
    has_icon_equivalent = bool(cue.icon_id and cue.accessible_description.strip())
    if not (has_text or has_icon_equivalent):
        issues.append("accessible_state_color_only")
    return tuple(issues)


def configure_accessible_widget(
    widget: AccessibleWidget,
    *,
    name: str,
    description: str = "",
    state_text: str = "",
    focus_policy: object | None = None,
) -> None:
    """Apply accessible metadata without importing PyQt or changing default focus implicitly."""

    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("accessible name must be non-empty")
    widget.setAccessibleName(normalized_name)
    widget.setAccessibleDescription(_join_description(description, state_text))
    if focus_policy is not None:
        setter = getattr(widget, "setFocusPolicy", None)
        if not callable(setter):
            raise TypeError("widget does not support focus policy")
        setter(focus_policy)


def update_accessible_state(widget: AccessibleWidget, state_text: str, *, description: str = "") -> None:
    normalized_state = state_text.strip()
    if not normalized_state:
        raise ValueError("accessible state text must be non-empty")
    widget.setAccessibleDescription(_join_description(description, normalized_state))


def scaled_pixels(value: int, scale: float) -> int:
    """Scale a layout probe deterministically for 100/150/200% DPI tests."""

    if value < 0 or not math.isfinite(scale) or scale <= 0:
        raise ValueError("size and scale must be finite positive values")
    return int(math.ceil(value * scale))


def _join_description(description: str, state_text: str) -> str:
    return "；".join(part.strip() for part in (description, state_text) if part.strip())


def _composite(foreground: RgbColor, background: RgbColor) -> RgbColor:
    if foreground.alpha == 255:
        return foreground
    alpha = foreground.alpha / 255
    return RgbColor(
        *(
            round(channel * alpha + base * (1 - alpha))
            for channel, base in zip(foreground.channels[:3], background.channels[:3])
        ),
        255,
    )


def _relative_luminance(color: RgbColor) -> float:
    channels = []
    for channel in color.channels[:3]:
        normalized = channel / 255
        channels.append(normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


__all__ = [
    "AccessibilityHintsSnapshot",
    "AccessibilityHintsSource",
    "AccessibleStateCue",
    "AccessibleWidget",
    "ContrastIssue",
    "ContrastPair",
    "ContrastRole",
    "ContrastValidationResult",
    "RgbColor",
    "UnavailableAccessibilityHintsSource",
    "configure_accessible_widget",
    "contrast_ratio",
    "parse_color",
    "scaled_pixels",
    "update_accessible_state",
    "validate_contrast_pairs",
    "validate_state_cue",
]
