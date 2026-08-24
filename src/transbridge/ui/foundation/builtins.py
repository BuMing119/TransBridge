"""Built-in declarative TransBridge themes."""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    THEME_SCHEMA_VERSION,
    ColorReference,
    DomainTokens,
    NamedColor,
    PrimitiveTokens,
    RadiusTokens,
    ResourceBudget,
    RgbaColor,
    SemanticTokens,
    SizeTokens,
    SpacingTokens,
    StateStyle,
    ThemeCompatibility,
    ThemeDefinition,
    ThemeManifest,
    ThemeScheme,
    ThemeTokens,
    TypographyTokens,
)
from .registry import ThemeRegistry

DEFAULT_THEME_ID = "transbridge.default"
COMPATIBILITY_THEME_ID = "transbridge.compat-light"
BUILTIN_PROVIDER_ID = "transbridge.builtin"
COMPATIBILITY_PROVIDER_ID = "transbridge.builtin-compat"
COMPATIBILITY_REMOVAL_GATE = "fr24-s09-migration-inventory-complete"

_RESOURCE_BUDGET = ResourceBudget(max_resources=64, max_item_bytes=512 * 1024, max_total_bytes=4 * 1024 * 1024)
_TYPOGRAPHY = TypographyTokens(
    families=("Segoe UI Variable Text", "Microsoft YaHei UI", "Segoe UI", "Noto Sans CJK SC", "sans-serif"),
    body_size=10.0,
    small_size=9.0,
    heading_size=14.0,
    line_height=1.4,
    weight_regular=400,
    weight_bold=600,
)
_SPACING = SpacingTokens(xs=4.0, sm=8.0, md=12.0, lg=16.0, xl=24.0)
_RADII = RadiusTokens(sm=3.0, md=6.0, lg=10.0, pill=999.0)
_SIZES = SizeTokens(control_height=32.0, icon_sm=16.0, icon_md=20.0, icon_lg=24.0, focus_width=2.0)


@dataclass(frozen=True, slots=True)
class BuiltinThemeProvider:
    _manifest: ThemeManifest
    _definitions: tuple[ThemeDefinition, ...]

    def manifest(self) -> ThemeManifest:
        return self._manifest

    def load(self, theme_id: str, scheme: ThemeScheme) -> ThemeDefinition:
        if theme_id != self._manifest.theme_id:
            raise KeyError("unknown built-in theme")
        for definition in self._definitions:
            if definition.scheme is scheme:
                return definition
        raise KeyError("unsupported built-in scheme")


def builtin_providers() -> tuple[BuiltinThemeProvider, ...]:
    default_manifest = ThemeManifest(
        schema_version=THEME_SCHEMA_VERSION,
        provider_id=BUILTIN_PROVIDER_ID,
        theme_id=DEFAULT_THEME_ID,
        version="1.0.0",
        display_name="TransBridge Default",
        supported_schemes=(ThemeScheme.LIGHT, ThemeScheme.DARK),
        resource_budget=_RESOURCE_BUDGET,
    )
    compatibility_manifest = ThemeManifest(
        schema_version=THEME_SCHEMA_VERSION,
        provider_id=COMPATIBILITY_PROVIDER_ID,
        theme_id=COMPATIBILITY_THEME_ID,
        version="1.0.0",
        display_name="TransBridge Compatibility Light",
        supported_schemes=(ThemeScheme.LIGHT,),
        resource_budget=_RESOURCE_BUDGET,
        compatibility=ThemeCompatibility(
            fallback_only=True,
            removal_gate=COMPATIBILITY_REMOVAL_GATE,
        ),
    )
    return (
        BuiltinThemeProvider(
            default_manifest,
            (
                ThemeDefinition.create(default_manifest, ThemeScheme.LIGHT, _light_tokens()),
                ThemeDefinition.create(default_manifest, ThemeScheme.DARK, _dark_tokens()),
            ),
        ),
        BuiltinThemeProvider(
            compatibility_manifest,
            (ThemeDefinition.create(compatibility_manifest, ThemeScheme.LIGHT, _compatibility_tokens()),),
        ),
    )


def register_builtin_themes(registry: ThemeRegistry) -> tuple[object, ...]:
    """Register trusted framework themes through the same validation pipeline."""
    return tuple(registry._register_builtin(provider) for provider in builtin_providers())


def create_builtin_registry() -> ThemeRegistry:
    registry = ThemeRegistry()
    results = register_builtin_themes(registry)
    if any(result.status.value != "registered" for result in results):
        raise RuntimeError("built-in theme validation failed")
    return registry


def _light_tokens() -> ThemeTokens:
    colors = {
        "window": "#F6F8FB",
        "surface": "#FFFFFF",
        "surface-alt": "#EDF2F7",
        "text": "#172033",
        "text-muted": "#526173",
        "border": "#C9D3E0",
        "focus": "#2563EB",
        "selection": "#2563EB",
        "on-selection": "#FFFFFF",
        "disabled-text": "#718096",
        "disabled-surface": "#E7ECF2",
        "link": "#1D4ED8",
        "success": "#176B37",
        "warning": "#8A4B00",
        "error": "#B42318",
        "info": "#075E9B",
        "success-bg": "#E5F5EA",
        "warning-bg": "#FFF0D4",
        "error-bg": "#FCE8E6",
        "info-bg": "#E5F2FC",
        "neutral-bg": "#EDF2F7",
        "neutral": "#526173",
        "accent-bg": "#E8F0FE",
    }
    return _tokens(colors)


def _dark_tokens() -> ThemeTokens:
    colors = {
        "window": "#171A1F",
        "surface": "#20242B",
        "surface-alt": "#2A3038",
        "text": "#F4F6F8",
        "text-muted": "#BBC5CF",
        "border": "#8995A3",
        "focus": "#73BFFF",
        "selection": "#005A9E",
        "on-selection": "#FFFFFF",
        "disabled-text": "#8D98A5",
        "disabled-surface": "#303640",
        "link": "#86C8FF",
        "success": "#8DDBA7",
        "warning": "#FFD08A",
        "error": "#FFB4AB",
        "info": "#8DCCFF",
        "success-bg": "#173A24",
        "warning-bg": "#432D0D",
        "error-bg": "#461D1A",
        "info-bg": "#13324A",
        "neutral-bg": "#303640",
        "neutral": "#BBC5CF",
        "accent-bg": "#173653",
    }
    return _tokens(colors)


def _compatibility_tokens() -> ThemeTokens:
    colors = {
        "window": "#F0F0F0",
        "surface": "#FFFFFF",
        "surface-alt": "#E5E5E5",
        "text": "#202020",
        "text-muted": "#505050",
        "border": "#707070",
        "focus": "#005A9E",
        "selection": "#005A9E",
        "on-selection": "#FFFFFF",
        "disabled-text": "#666666",
        "disabled-surface": "#DDDDDD",
        "link": "#005A9E",
        "success": "#176B37",
        "warning": "#7A4600",
        "error": "#A4262C",
        "info": "#075E9B",
        "success-bg": "#E2F2E8",
        "warning-bg": "#FFF0D4",
        "error-bg": "#F9E5E7",
        "info-bg": "#E2EFF9",
        "neutral-bg": "#E5E5E5",
        "neutral": "#505050",
        "accent-bg": "#E1EEF8",
    }
    return _tokens(colors)


def _tokens(colors: dict[str, str]) -> ThemeTokens:
    primitive_colors = tuple(NamedColor(name, RgbaColor.from_hex(value)) for name, value in sorted(colors.items()))
    ref = ColorReference
    semantic = SemanticTokens(
        window=ref("window"),
        surface=ref("surface"),
        surface_alt=ref("surface-alt"),
        text_primary=ref("text"),
        text_secondary=ref("text-muted"),
        border=ref("border"),
        focus=ref("focus"),
        selection_background=ref("selection"),
        selection_text=ref("on-selection"),
        disabled_text=ref("disabled-text"),
        disabled_surface=ref("disabled-surface"),
        link=ref("link"),
        success=ref("success"),
        warning=ref("warning"),
        error=ref("error"),
        info=ref("info"),
    )
    primitives = PrimitiveTokens(primitive_colors, _TYPOGRAPHY, _SPACING, _RADII, _SIZES)
    return ThemeTokens(primitives=primitives, semantic=semantic, domain=_domain_tokens())


def _domain_tokens() -> DomainTokens:
    stages = (
        _state("-1", "neutral", "neutral-bg", "translation.stage.hidden", "status.hidden"),
        _state("0", "neutral", "neutral-bg", "translation.stage.untranslated", "status.pending"),
        _state("1", "info", "info-bg", "translation.stage.translated", "status.translated"),
        _state("2", "warning", "warning-bg", "translation.stage.questionable", "status.questionable"),
        _state("3", "info", "info-bg", "translation.stage.checked", "status.checked"),
        _state("5", "success", "success-bg", "translation.stage.reviewed", "status.reviewed"),
        _state("9", "error", "error-bg", "translation.stage.locked", "status.locked"),
    )
    labels = (
        _state("neutral", "neutral", "neutral-bg", "label.neutral"),
        _state("accent", "info", "accent-bg", "label.accent"),
        _state("success", "success", "success-bg", "label.success"),
        _state("warning", "warning", "warning-bg", "label.warning"),
        _state("error", "error", "error-bg", "label.error"),
    )
    diff = (
        _state("added", "success", "success-bg", "diff.added", "diff.added"),
        _state("removed", "error", "error-bg", "diff.removed", "diff.removed"),
        _state("changed", "warning", "warning-bg", "diff.changed", "diff.changed"),
        _state("unchanged", "neutral", "neutral-bg", "diff.unchanged", "diff.unchanged"),
    )
    translation = (
        _state("source", "neutral", "neutral-bg", "translation.source"),
        _state("translated", "info", "info-bg", "translation.translated"),
        _state("questionable", "warning", "warning-bg", "translation.questionable", "status.questionable"),
        _state("checked", "info", "info-bg", "translation.checked", "status.checked"),
        _state("reviewed", "success", "success-bg", "translation.reviewed", "status.reviewed"),
        _state("locked", "error", "error-bg", "translation.locked", "status.locked"),
        _state("hidden", "neutral", "neutral-bg", "translation.hidden", "status.hidden"),
    )
    task = (
        _state("queued", "neutral", "neutral-bg", "task.queued", "task.queued"),
        _state("running", "info", "info-bg", "task.running", "task.running"),
        _state("paused", "warning", "warning-bg", "task.paused", "task.paused"),
        _state("cancelling", "warning", "warning-bg", "task.cancelling", "task.cancelling"),
        _state("cancelled", "neutral", "neutral-bg", "task.cancelled", "task.cancelled"),
        _state("completed", "success", "success-bg", "task.completed", "task.completed"),
        _state("failed", "error", "error-bg", "task.failed", "task.failed"),
    )
    report = (
        _state("info", "info", "info-bg", "report.info", "report.info"),
        _state("success", "success", "success-bg", "report.success", "report.success"),
        _state("warning", "warning", "warning-bg", "report.warning", "report.warning"),
        _state("error", "error", "error-bg", "report.error", "report.error"),
    )
    return DomainTokens(stages=stages, labels=labels, diff=diff, translation=translation, task=task, report=report)


def _state(key: str, foreground: str, background: str, label_key: str, icon_id: str | None = None) -> StateStyle:
    return StateStyle(
        key=key,
        foreground=ColorReference(foreground),
        background=ColorReference(background),
        border=ColorReference(foreground),
        label_key=label_key,
        icon_id=icon_id,
    )
