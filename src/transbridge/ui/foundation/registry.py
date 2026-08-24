"""Validation and copy-on-write registration for Qt-free themes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from urllib.parse import urlparse

from .model import (
    THEME_SCHEMA_VERSION,
    ColorReference,
    DomainTokens,
    NamedColor,
    PrimitiveTokens,
    RegistrationResult,
    RegistrationStatus,
    RgbaColor,
    SemanticTokens,
    StateStyle,
    ThemeDefinition,
    ThemeError,
    ThemeErrorCode,
    ThemeManifest,
    ThemeProvider,
    ThemeScheme,
    ThemeTokens,
    ValidationIssue,
    theme_fingerprint,
)

_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_RESOURCE_TYPES = frozenset({"image/png", "image/svg+xml", "font/ttf", "font/otf"})
_MAX_RESOURCES = 256
_MAX_ITEM_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 8 * 1024 * 1024

_DOMAIN_REQUIREMENTS = {
    "stages": frozenset({"-1", "0", "1", "2", "3", "5", "9"}),
    "labels": frozenset({"neutral", "accent", "success", "warning", "error"}),
    "diff": frozenset({"added", "removed", "changed", "unchanged"}),
    "translation": frozenset({"source", "translated", "questionable", "checked", "reviewed", "locked", "hidden"}),
    "task": frozenset({"queued", "running", "paused", "cancelling", "cancelled", "completed", "failed"}),
    "report": frozenset({"info", "success", "warning", "error"}),
}


class ThemeValidator:
    """Validate and normalize a theme once, before it reaches runtime paths."""

    def validate_manifest(self, manifest: ThemeManifest, *, trusted_builtin: bool = False) -> None:
        issues: list[ValidationIssue] = []
        self._validate_manifest(manifest, trusted_builtin=trusted_builtin, issues=issues)
        if issues:
            raise ThemeValidationError(tuple(issues))

    def validate(
        self, manifest: ThemeManifest, definition: ThemeDefinition, *, trusted_builtin: bool = False
    ) -> ThemeDefinition:
        issues: list[ValidationIssue] = []
        self._validate_manifest(manifest, trusted_builtin=trusted_builtin, issues=issues)
        self._validate_definition_identity(manifest, definition, issues)
        self._validate_structure(definition, issues)
        self._validate_resources(manifest, definition, issues)

        normalized = self._normalize_tokens(definition, issues)
        if normalized is not None:
            self._validate_contrast(normalized, issues)

        if issues:
            raise ThemeValidationError(tuple(issues))
        assert normalized is not None
        normalized = replace(normalized, fingerprint="")
        return replace(normalized, fingerprint=theme_fingerprint(normalized))

    def _validate_manifest(
        self,
        manifest: ThemeManifest,
        *,
        trusted_builtin: bool,
        issues: list[ValidationIssue],
    ) -> None:
        if not isinstance(manifest, ThemeManifest):
            issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "provider manifest has an invalid type", "manifest"))
            return
        if manifest.schema_version != THEME_SCHEMA_VERSION:
            issues.append(
                _issue(
                    ThemeErrorCode.SCHEMA_UNSUPPORTED,
                    "theme schema version is not supported",
                    "manifest.schema_version",
                )
            )
        for name, value in (("provider_id", manifest.provider_id), ("theme_id", manifest.theme_id)):
            if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, f"{name} is not a stable lowercase ID", f"manifest.{name}")
                )
        if (
            isinstance(manifest.provider_id, str)
            and isinstance(manifest.theme_id, str)
            and (manifest.provider_id.startswith("transbridge.") or manifest.theme_id.startswith("transbridge."))
            and not trusted_builtin
        ):
            issues.append(
                _issue(ThemeErrorCode.ID_CONFLICT, "reserved TransBridge namespace is not available", "manifest")
            )
        if not isinstance(manifest.version, str) or not _VERSION_PATTERN.fullmatch(manifest.version):
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "theme version must be semantic x.y.z", "manifest.version")
            )
        if not isinstance(manifest.display_name, str) or not manifest.display_name.strip():
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "theme display name is required", "manifest.display_name")
            )
        schemes = manifest.supported_schemes
        if (
            not isinstance(schemes, tuple)
            or not schemes
            or len(set(schemes)) != len(schemes)
            or any(not isinstance(scheme, ThemeScheme) for scheme in schemes)
        ):
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "supported schemes are invalid", "manifest.supported_schemes")
            )
        budget = manifest.resource_budget
        if not hasattr(budget, "max_resources"):
            issues.append(
                _issue(
                    ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED,
                    "resource budget has an invalid structure",
                    "manifest.resource_budget",
                )
            )
            return
        values = (budget.max_resources, budget.max_item_bytes, budget.max_total_bytes)
        if any(not _positive_int(value) for value in values):
            issues.append(
                _issue(
                    ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED,
                    "resource budget must be positive",
                    "manifest.resource_budget",
                )
            )
        elif (
            budget.max_resources > _MAX_RESOURCES
            or budget.max_item_bytes > _MAX_ITEM_BYTES
            or budget.max_total_bytes > _MAX_TOTAL_BYTES
            or budget.max_item_bytes > budget.max_total_bytes
        ):
            issues.append(
                _issue(
                    ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED,
                    "resource budget exceeds framework limits",
                    "manifest.resource_budget",
                )
            )
        compatibility = manifest.compatibility
        if compatibility.fallback_only:
            if not trusted_builtin:
                issues.append(
                    _issue(
                        ThemeErrorCode.ID_CONFLICT,
                        "compatibility themes are reserved for built-ins",
                        "manifest.compatibility",
                    )
                )
            if not isinstance(compatibility.removal_gate, str) or not compatibility.removal_gate.strip():
                issues.append(
                    _issue(
                        ThemeErrorCode.TOKENS_INVALID,
                        "compatibility theme requires a removal gate",
                        "manifest.compatibility",
                    )
                )

    def _validate_definition_identity(
        self,
        manifest: ThemeManifest,
        definition: ThemeDefinition,
        issues: list[ValidationIssue],
    ) -> None:
        if not isinstance(definition, ThemeDefinition):
            issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "provider returned an invalid definition", "theme"))
            return
        if definition.manifest != manifest:
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "definition manifest does not match provider", "theme.manifest")
            )
        if definition.scheme not in manifest.supported_schemes:
            issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "definition scheme is not declared", "theme.scheme"))
        if definition.fingerprint:
            try:
                expected = theme_fingerprint(replace(definition, fingerprint=""))
            except (TypeError, ValueError):
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, "definition cannot be fingerprinted", "theme.fingerprint")
                )
            else:
                if definition.fingerprint != expected:
                    issues.append(
                        _issue(
                            ThemeErrorCode.TOKENS_INVALID,
                            "definition fingerprint does not match content",
                            "theme.fingerprint",
                        )
                    )

    def _validate_structure(self, definition: ThemeDefinition, issues: list[ValidationIssue]) -> None:
        if not isinstance(definition, ThemeDefinition) or not isinstance(definition.tokens, ThemeTokens):
            issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "theme tokens have an invalid structure", "tokens"))
            return
        primitives = definition.tokens.primitives
        if (
            not isinstance(primitives, PrimitiveTokens)
            or not isinstance(primitives.colors, tuple)
            or not primitives.colors
        ):
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "primitive colors are required", "tokens.primitives.colors")
            )
            return
        names = [token.name for token in primitives.colors if isinstance(token, NamedColor)]
        if (
            len(names) != len(primitives.colors)
            or len(names) != len(set(names))
            or any(not _ID_PATTERN.fullmatch(name) for name in names)
        ):
            issues.append(
                _issue(
                    ThemeErrorCode.TOKENS_INVALID,
                    "primitive color names must be unique stable IDs",
                    "tokens.primitives.colors",
                )
            )
        self._validate_structural_numbers(primitives, issues)
        domain = definition.tokens.domain
        if not isinstance(domain, DomainTokens):
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "domain tokens have an invalid structure", "tokens.domain")
            )
            return
        for category, required in _DOMAIN_REQUIREMENTS.items():
            styles = getattr(domain, category)
            if not isinstance(styles, tuple) or any(not isinstance(style, StateStyle) for style in styles):
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, "domain category is invalid", f"tokens.domain.{category}")
                )
                continue
            keys = [style.key for style in styles]
            if len(keys) != len(set(keys)):
                issues.append(
                    _issue(
                        ThemeErrorCode.TOKENS_INVALID, "domain state keys must be unique", f"tokens.domain.{category}"
                    )
                )
            missing = required.difference(keys)
            if missing:
                issues.append(
                    _issue(
                        ThemeErrorCode.TOKENS_INVALID,
                        f"domain category is missing required states: {', '.join(sorted(missing))}",
                        f"tokens.domain.{category}",
                    )
                )
            for style in styles:
                if (
                    not isinstance(style.key, str)
                    or not style.key
                    or not isinstance(style.label_key, str)
                    or not style.label_key.strip()
                ):
                    issues.append(
                        _issue(
                            ThemeErrorCode.TOKENS_INVALID,
                            "domain state needs a key and text cue",
                            f"tokens.domain.{category}",
                        )
                    )
                if style.icon_id is not None and (
                    not isinstance(style.icon_id, str) or not _ID_PATTERN.fullmatch(style.icon_id)
                ):
                    issues.append(
                        _issue(ThemeErrorCode.TOKENS_INVALID, "domain icon ID is invalid", f"tokens.domain.{category}")
                    )

    def _validate_structural_numbers(self, primitives: PrimitiveTokens, issues: list[ValidationIssue]) -> None:
        for group_name in ("typography", "spacing", "radii", "sizes"):
            group = getattr(primitives, group_name)
            if not hasattr(group, "__dataclass_fields__"):
                issues.append(
                    _issue(
                        ThemeErrorCode.TOKENS_INVALID,
                        "token group has an invalid type",
                        f"tokens.primitives.{group_name}",
                    )
                )
                continue
            for field in fields(group):
                value = getattr(group, field.name)
                if field.name == "families":
                    if (
                        not isinstance(value, tuple)
                        or not value
                        or any(not isinstance(item, str) or not item.strip() for item in value)
                    ):
                        issues.append(
                            _issue(
                                ThemeErrorCode.TOKENS_INVALID,
                                "font families are invalid",
                                "tokens.primitives.typography.families",
                            )
                        )
                elif not _finite_non_negative(value) or (field.name not in {"xs", "sm"} and value == 0):
                    issues.append(
                        _issue(
                            ThemeErrorCode.TOKENS_INVALID,
                            "numeric token is outside its valid range",
                            f"tokens.primitives.{group_name}.{field.name}",
                        )
                    )

    def _validate_resources(
        self,
        manifest: ThemeManifest,
        definition: ThemeDefinition,
        issues: list[ValidationIssue],
    ) -> None:
        if not isinstance(definition, ThemeDefinition) or not isinstance(definition.resources, tuple):
            issues.append(
                _issue(ThemeErrorCode.TOKENS_INVALID, "theme resources must be an immutable tuple", "resources")
            )
            return
        resources = definition.resources
        budget = manifest.resource_budget
        if len(resources) > budget.max_resources:
            issues.append(
                _issue(ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED, "theme contains too many resources", "resources")
            )
        total = 0
        logical_ids: set[str] = set()
        for resource in resources:
            if not _ID_PATTERN.fullmatch(resource.logical_id) or resource.logical_id in logical_ids:
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, "resource logical ID is invalid or duplicated", "resources")
                )
            logical_ids.add(resource.logical_id)
            path = resource.relative_path
            parsed = urlparse(path)
            pure_path = PurePosixPath(path)
            if (
                not isinstance(path, str)
                or not path
                or parsed.scheme
                or parsed.netloc
                or pure_path.is_absolute()
                or ".." in pure_path.parts
                or "." in pure_path.parts
                or "\\" in path
            ):
                issues.append(
                    _issue(
                        ThemeErrorCode.TOKENS_INVALID, "resource path is not a safe package-relative path", "resources"
                    )
                )
            if resource.media_type not in _ALLOWED_RESOURCE_TYPES:
                issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "resource media type is not allowed", "resources"))
            if not _positive_int(resource.byte_size):
                issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "resource byte size is invalid", "resources"))
            else:
                total += resource.byte_size
                if resource.byte_size > budget.max_item_bytes:
                    issues.append(
                        _issue(ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED, "resource exceeds per-item budget", "resources")
                    )
            if not isinstance(resource.checksum, str) or not _CHECKSUM_PATTERN.fullmatch(resource.checksum):
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, "resource checksum must be lowercase SHA-256", "resources")
                )
        if total > budget.max_total_bytes:
            issues.append(
                _issue(ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED, "resources exceed total byte budget", "resources")
            )

    def _normalize_tokens(
        self,
        definition: ThemeDefinition,
        issues: list[ValidationIssue],
    ) -> ThemeDefinition | None:
        if not isinstance(definition, ThemeDefinition) or not isinstance(definition.tokens, ThemeTokens):
            return None
        primitives = definition.tokens.primitives
        semantic = definition.tokens.semantic
        domain = definition.tokens.domain
        if (
            not isinstance(primitives, PrimitiveTokens)
            or not isinstance(semantic, SemanticTokens)
            or not isinstance(domain, DomainTokens)
        ):
            issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "theme token layers have invalid types", "tokens"))
            return None

        named = {token.name: token.value for token in primitives.colors if isinstance(token, NamedColor)}
        resolved: dict[str, RgbaColor] = {}
        visiting: set[str] = set()

        def resolve(value: object, location: str) -> RgbaColor | None:
            if isinstance(value, RgbaColor):
                channels = (value.red, value.green, value.blue, value.alpha)
                if any(
                    isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255
                    for channel in channels
                ):
                    issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "RGBA channel is outside 0..255", location))
                    return None
                return value
            if not isinstance(value, ColorReference) or not isinstance(value.name, str):
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, "color token is not canonical RGBA or a reference", location)
                )
                return None
            if value.name in resolved:
                return resolved[value.name]
            if value.name in visiting:
                issues.append(_issue(ThemeErrorCode.TOKENS_INVALID, "primitive color reference is recursive", location))
                return None
            target = named.get(value.name)
            if target is None:
                issues.append(
                    _issue(ThemeErrorCode.TOKENS_INVALID, "primitive color reference is unresolved", location)
                )
                return None
            visiting.add(value.name)
            color = resolve(target, f"tokens.primitives.colors.{value.name}")
            visiting.remove(value.name)
            if color is not None:
                resolved[value.name] = color
            return color

        normalized_colors: list[NamedColor] = []
        for token in primitives.colors:
            color = resolve(token.value, f"tokens.primitives.colors.{token.name}")
            if color is not None:
                resolved[token.name] = color
                normalized_colors.append(replace(token, value=color))

        semantic_values = {}
        for field in fields(semantic):
            color = resolve(getattr(semantic, field.name), f"tokens.semantic.{field.name}")
            if color is not None:
                semantic_values[field.name] = color
        if len(semantic_values) != len(fields(semantic)):
            return None
        normalized_semantic = replace(semantic, **semantic_values)

        domain_values: dict[str, tuple[StateStyle, ...]] = {}
        for category in _DOMAIN_REQUIREMENTS:
            styles: list[StateStyle] = []
            for style in getattr(domain, category):
                values = {
                    field_name: resolve(
                        getattr(style, field_name), f"tokens.domain.{category}.{style.key}.{field_name}"
                    )
                    for field_name in ("foreground", "background", "border")
                }
                if all(value is not None for value in values.values()):
                    styles.append(replace(style, **values))
            domain_values[category] = tuple(styles)
        normalized_domain = replace(domain, **domain_values)
        normalized_primitives = replace(primitives, colors=tuple(normalized_colors))
        normalized_tokens = replace(
            definition.tokens,
            primitives=normalized_primitives,
            semantic=normalized_semantic,
            domain=normalized_domain,
        )
        return replace(definition, tokens=normalized_tokens)

    def _validate_contrast(self, definition: ThemeDefinition, issues: list[ValidationIssue]) -> None:
        semantic = definition.tokens.semantic
        if not all(color.alpha == 255 for color in (semantic.window, semantic.surface, semantic.surface_alt)):
            issues.append(
                _issue(
                    ThemeErrorCode.TOKENS_INVALID,
                    "critical semantic backgrounds must be opaque",
                    "tokens.semantic",
                )
            )
        combinations = (
            ("text_primary/window", semantic.text_primary, semantic.window, 4.5),
            ("text_primary/surface", semantic.text_primary, semantic.surface, 4.5),
            ("selection_text/selection_background", semantic.selection_text, semantic.selection_background, 4.5),
            ("focus/window", semantic.focus, semantic.window, 3.0),
            ("link/window", semantic.link, semantic.window, 3.0),
        )
        for name, foreground, background, threshold in combinations:
            if not isinstance(foreground, RgbaColor) or not isinstance(background, RgbaColor):
                continue
            ratio = contrast_ratio(foreground, background)
            if ratio + 1e-9 < threshold:
                issues.append(
                    _issue(
                        ThemeErrorCode.TOKENS_INVALID,
                        f"critical contrast {name} is below {threshold:g}:1",
                        "tokens.semantic",
                    )
                )
        for category in _DOMAIN_REQUIREMENTS:
            for style in getattr(definition.tokens.domain, category):
                background = _composite(style.background, semantic.surface)
                if contrast_ratio(style.foreground, background) + 1e-9 < 4.5:
                    issues.append(
                        _issue(
                            ThemeErrorCode.TOKENS_INVALID,
                            f"domain contrast {category}.{style.key} is below 4.5:1",
                            f"tokens.domain.{category}.{style.key}",
                        )
                    )


class ThemeValidationError(ValueError):
    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        super().__init__("theme validation failed")
        self.issues = issues


class ThemeRegistry:
    """Atomic registry of fully validated theme definitions."""

    def __init__(self, validator: ThemeValidator | None = None) -> None:
        self._validator = validator or ThemeValidator()
        self._providers: Mapping[str, ThemeProvider] = MappingProxyType({})
        self._themes: Mapping[tuple[str, ThemeScheme], ThemeDefinition] = MappingProxyType({})

    def register(self, provider: ThemeProvider) -> RegistrationResult:
        return self._register(provider, trusted_builtin=False)

    def _register_builtin(self, provider: ThemeProvider) -> RegistrationResult:
        return self._register(provider, trusted_builtin=True)

    def _register(self, provider: ThemeProvider, *, trusted_builtin: bool) -> RegistrationResult:
        try:
            manifest = provider.manifest()
        except Exception:  # noqa: BLE001 - provider details must not escape the registry boundary
            return _rejected(ThemeErrorCode.PROVIDER_FAILED, "theme provider manifest failed")

        provider_id = manifest.provider_id if isinstance(manifest, ThemeManifest) else None
        theme_id = manifest.theme_id if isinstance(manifest, ThemeManifest) else None
        try:
            self._validator.validate_manifest(manifest, trusted_builtin=trusted_builtin)
            definitions = tuple(
                self._validator.validate(
                    manifest,
                    provider.load(manifest.theme_id, scheme),
                    trusted_builtin=trusted_builtin,
                )
                for scheme in manifest.supported_schemes
            )
        except ThemeValidationError as exc:
            return RegistrationResult(
                status=RegistrationStatus.REJECTED,
                provider_id=provider_id,
                theme_id=theme_id,
                error_code=_primary_error(exc.issues),
                diagnostics=exc.issues,
            )
        except Exception:  # noqa: BLE001 - provider details must not escape the registry boundary
            return _rejected(ThemeErrorCode.PROVIDER_FAILED, "theme provider load failed", provider_id, theme_id)

        keys = tuple((definition.manifest.theme_id, definition.scheme) for definition in definitions)
        existing = tuple(self._themes.get(key) for key in keys)
        if all(
            item is not None and item.fingerprint == definition.fingerprint
            for item, definition in zip(existing, definitions, strict=True)
        ):
            existing_provider = self._providers.get(manifest.provider_id)
            if existing_provider is not None:
                return RegistrationResult(RegistrationStatus.UNCHANGED, manifest.provider_id, manifest.theme_id)
        theme_id_exists = any(key[0] == manifest.theme_id for key in self._themes)
        if manifest.provider_id in self._providers or theme_id_exists or any(item is not None for item in existing):
            return _rejected(
                ThemeErrorCode.ID_CONFLICT,
                "provider or theme ID conflicts with registered content",
                provider_id,
                theme_id,
            )

        next_providers = dict(self._providers)
        next_themes = dict(self._themes)
        next_providers[manifest.provider_id] = provider
        next_themes.update(zip(keys, definitions, strict=True))
        self._providers = MappingProxyType(next_providers)
        self._themes = MappingProxyType(next_themes)
        return RegistrationResult(RegistrationStatus.REGISTERED, manifest.provider_id, manifest.theme_id)

    def resolve(self, theme_id: str, scheme: ThemeScheme) -> ThemeDefinition:
        try:
            return self._themes[(theme_id, scheme)]
        except (KeyError, TypeError) as exc:
            raise ThemeError(ThemeErrorCode.UNKNOWN, "requested theme is not registered") from exc

    def unregister(self, provider_id: str) -> RegistrationResult:
        provider = self._providers.get(provider_id)
        if provider is None:
            return RegistrationResult(RegistrationStatus.UNCHANGED, provider_id=provider_id)
        try:
            manifest = provider.manifest()
        except Exception:  # noqa: BLE001 - a broken provider still has a registry identity
            manifest = None
        next_providers = dict(self._providers)
        next_themes = {
            key: definition
            for key, definition in self._themes.items()
            if definition.manifest.provider_id != provider_id
        }
        del next_providers[provider_id]
        self._providers = MappingProxyType(next_providers)
        self._themes = MappingProxyType(next_themes)
        return RegistrationResult(
            RegistrationStatus.REGISTERED,
            provider_id=provider_id,
            theme_id=manifest.theme_id if isinstance(manifest, ThemeManifest) else None,
        )

    @property
    def themes(self) -> tuple[ThemeDefinition, ...]:
        return tuple(self._themes[key] for key in sorted(self._themes, key=lambda item: (item[0], item[1].value)))


def contrast_ratio(foreground: RgbaColor, background: RgbaColor) -> float:
    """Calculate WCAG contrast after compositing foreground over its declared background."""
    composited = _composite(foreground, background)
    lighter = max(_luminance(composited), _luminance(background))
    darker = min(_luminance(composited), _luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def _composite(foreground: RgbaColor, background: RgbaColor) -> RgbaColor:
    alpha = foreground.alpha / 255
    return RgbaColor(
        *(
            round(channel * alpha + base * (1 - alpha))
            for channel, base in zip(_rgb(foreground), _rgb(background), strict=True)
        ),
        255,
    )


def _rgb(color: RgbaColor) -> tuple[int, int, int]:
    return color.red, color.green, color.blue


def _luminance(color: RgbaColor) -> float:
    values = tuple(channel / 255 for channel in _rgb(color))
    linear = tuple(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _finite_non_negative(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _issue(code: ThemeErrorCode, message: str, location: str = "theme") -> ValidationIssue:
    return ValidationIssue(code=code, message=message, location=location)


def _primary_error(issues: tuple[ValidationIssue, ...]) -> ThemeErrorCode:
    for code in (
        ThemeErrorCode.SCHEMA_UNSUPPORTED,
        ThemeErrorCode.ID_CONFLICT,
        ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED,
        ThemeErrorCode.TOKENS_INVALID,
    ):
        if any(issue.code is code for issue in issues):
            return code
    return ThemeErrorCode.TOKENS_INVALID


def _rejected(
    code: ThemeErrorCode,
    message: str,
    provider_id: str | None = None,
    theme_id: str | None = None,
) -> RegistrationResult:
    issue = _issue(code, message)
    return RegistrationResult(RegistrationStatus.REJECTED, provider_id, theme_id, code, (issue,))
