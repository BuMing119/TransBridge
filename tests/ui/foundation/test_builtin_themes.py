from __future__ import annotations

from dataclasses import fields

from transbridge.application.tasks.models import JobState
from transbridge.converter.translation_entry import STAGE_LABELS
from transbridge.ui.foundation import (
    COMPATIBILITY_REMOVAL_GATE,
    COMPATIBILITY_THEME_ID,
    DEFAULT_THEME_ID,
    RegistrationStatus,
    RgbaColor,
    ThemeScheme,
    builtin_providers,
    contrast_ratio,
    create_builtin_registry,
)


def test_all_builtin_themes_register_and_have_canonical_runtime_colors() -> None:
    registry = create_builtin_registry()

    assert {(theme.manifest.theme_id, theme.scheme) for theme in registry.themes} == {
        (DEFAULT_THEME_ID, ThemeScheme.LIGHT),
        (DEFAULT_THEME_ID, ThemeScheme.DARK),
        (COMPATIBILITY_THEME_ID, ThemeScheme.LIGHT),
    }
    for theme in registry.themes:
        assert len(theme.fingerprint) == 64
        assert all(
            isinstance(getattr(theme.tokens.semantic, field.name), RgbaColor) for field in fields(theme.tokens.semantic)
        )
        for category in fields(theme.tokens.domain):
            for style in getattr(theme.tokens.domain, category.name):
                assert isinstance(style.foreground, RgbaColor)
                assert isinstance(style.background, RgbaColor)
                assert isinstance(style.border, RgbaColor)
                assert style.label_key


def test_light_and_dark_share_structure_but_not_palette() -> None:
    registry = create_builtin_registry()
    light = registry.resolve(DEFAULT_THEME_ID, ThemeScheme.LIGHT)
    dark = registry.resolve(DEFAULT_THEME_ID, ThemeScheme.DARK)

    assert light.tokens.primitives.typography is dark.tokens.primitives.typography
    assert light.tokens.primitives.spacing is dark.tokens.primitives.spacing
    assert light.tokens.primitives.radii is dark.tokens.primitives.radii
    assert light.tokens.primitives.sizes is dark.tokens.primitives.sizes
    assert tuple(style.key for style in light.tokens.domain.stages) == tuple(
        style.key for style in dark.tokens.domain.stages
    )
    assert light.tokens.semantic.window != dark.tokens.semantic.window


def test_builtin_domain_tokens_cover_current_stage_task_and_report_states() -> None:
    registry = create_builtin_registry()
    for scheme in ThemeScheme:
        domain = registry.resolve(DEFAULT_THEME_ID, scheme).tokens.domain
        assert {style.key for style in domain.stages} == {str(stage) for stage in STAGE_LABELS}
        assert {style.key for style in domain.task} == {state.value for state in JobState}
        assert {style.key for style in domain.report} == {"info", "success", "warning", "error"}
        assert {style.key for style in domain.diff} == {"added", "removed", "changed", "unchanged"}
        assert {style.key for style in domain.translation} >= {
            "source",
            "translated",
            "questionable",
            "checked",
            "reviewed",
            "locked",
            "hidden",
        }


def test_builtin_critical_contrast_meets_declared_thresholds() -> None:
    registry = create_builtin_registry()
    for theme in registry.themes:
        semantic = theme.tokens.semantic
        assert contrast_ratio(semantic.text_primary, semantic.window) >= 4.5
        assert contrast_ratio(semantic.text_primary, semantic.surface) >= 4.5
        assert contrast_ratio(semantic.selection_text, semantic.selection_background) >= 4.5
        assert contrast_ratio(semantic.focus, semantic.window) >= 3.0
        for category in fields(theme.tokens.domain):
            for style in getattr(theme.tokens.domain, category.name):
                assert contrast_ratio(style.foreground, style.background) >= 4.5


def test_compatibility_theme_is_builtin_fallback_with_explicit_removal_gate() -> None:
    providers = builtin_providers()
    compatibility = next(
        provider.manifest() for provider in providers if provider.manifest().theme_id == COMPATIBILITY_THEME_ID
    )

    assert compatibility.compatibility.fallback_only is True
    assert compatibility.compatibility.removal_gate == COMPATIBILITY_REMOVAL_GATE
    assert compatibility.supported_schemes == (ThemeScheme.LIGHT,)

    external_registry_result = (
        __import__("transbridge.ui.foundation", fromlist=["ThemeRegistry"])
        .ThemeRegistry()
        .register(next(provider for provider in providers if provider.manifest().theme_id == COMPATIBILITY_THEME_ID))
    )
    assert external_registry_result.status is RegistrationStatus.REJECTED
