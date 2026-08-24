"""Qt-free immutable contracts for TransBridge themes."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
import hashlib
import json
from typing import Any, Protocol, runtime_checkable

THEME_SCHEMA_VERSION = 1


class ThemeScheme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class RegistrationStatus(StrEnum):
    REGISTERED = "registered"
    UNCHANGED = "unchanged"
    REJECTED = "rejected"


class ThemeErrorCode(StrEnum):
    UNKNOWN = "theme_unknown"
    PROVIDER_FAILED = "theme_provider_failed"
    SCHEMA_UNSUPPORTED = "theme_schema_unsupported"
    TOKENS_INVALID = "theme_tokens_invalid"
    RESOURCE_BUDGET_EXCEEDED = "theme_resource_budget_exceeded"
    ID_CONFLICT = "theme_id_conflict"


@dataclass(frozen=True, slots=True)
class RgbaColor:
    red: int
    green: int
    blue: int
    alpha: int = 255

    @classmethod
    def from_hex(cls, value: str) -> RgbaColor:
        """Parse a build-time color literal into runtime-ready RGBA channels."""
        if not isinstance(value, str) or not value.startswith("#") or len(value) not in {7, 9}:
            raise ValueError("color must use #RRGGBB or #RRGGBBAA")
        try:
            channels = tuple(int(value[index : index + 2], 16) for index in range(1, len(value), 2))
        except ValueError as exc:
            raise ValueError("color contains non-hexadecimal characters") from exc
        return cls(*channels) if len(channels) == 4 else cls(*channels, 255)

    @property
    def canonical(self) -> str:
        return f"#{self.red:02X}{self.green:02X}{self.blue:02X}{self.alpha:02X}"


@dataclass(frozen=True, slots=True)
class ColorReference:
    """Build-time reference to a named primitive color."""

    name: str


ColorValue = RgbaColor | ColorReference


@dataclass(frozen=True, slots=True)
class NamedColor:
    name: str
    value: ColorValue


@dataclass(frozen=True, slots=True)
class TypographyTokens:
    families: tuple[str, ...]
    body_size: float
    small_size: float
    heading_size: float
    line_height: float
    weight_regular: int
    weight_bold: int


@dataclass(frozen=True, slots=True)
class SpacingTokens:
    xs: float
    sm: float
    md: float
    lg: float
    xl: float


@dataclass(frozen=True, slots=True)
class RadiusTokens:
    sm: float
    md: float
    lg: float
    pill: float


@dataclass(frozen=True, slots=True)
class SizeTokens:
    control_height: float
    icon_sm: float
    icon_md: float
    icon_lg: float
    focus_width: float


@dataclass(frozen=True, slots=True)
class PrimitiveTokens:
    colors: tuple[NamedColor, ...]
    typography: TypographyTokens
    spacing: SpacingTokens
    radii: RadiusTokens
    sizes: SizeTokens

    def color(self, name: str) -> RgbaColor:
        for token in self.colors:
            if token.name == name and isinstance(token.value, RgbaColor):
                return token.value
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class SemanticTokens:
    window: ColorValue
    surface: ColorValue
    surface_alt: ColorValue
    text_primary: ColorValue
    text_secondary: ColorValue
    border: ColorValue
    focus: ColorValue
    selection_background: ColorValue
    selection_text: ColorValue
    disabled_text: ColorValue
    disabled_surface: ColorValue
    link: ColorValue
    success: ColorValue
    warning: ColorValue
    error: ColorValue
    info: ColorValue


@dataclass(frozen=True, slots=True)
class StateStyle:
    key: str
    foreground: ColorValue
    background: ColorValue
    border: ColorValue
    label_key: str
    icon_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomainTokens:
    stages: tuple[StateStyle, ...]
    labels: tuple[StateStyle, ...]
    diff: tuple[StateStyle, ...]
    translation: tuple[StateStyle, ...]
    task: tuple[StateStyle, ...]
    report: tuple[StateStyle, ...]

    def style(self, category: str, key: str | int) -> StateStyle:
        styles = getattr(self, category, None)
        if not isinstance(styles, tuple):
            raise KeyError(category)
        normalized_key = str(key)
        for style in styles:
            if style.key == normalized_key:
                return style
        raise KeyError(f"{category}.{normalized_key}")

    def stage(self, key: str | int) -> StateStyle:
        return self.style("stages", key)


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    primitives: PrimitiveTokens
    semantic: SemanticTokens
    domain: DomainTokens


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    max_resources: int
    max_item_bytes: int
    max_total_bytes: int


@dataclass(frozen=True, slots=True)
class ThemeCompatibility:
    fallback_only: bool = False
    removal_gate: str | None = None


@dataclass(frozen=True, slots=True)
class ThemeManifest:
    schema_version: int
    provider_id: str
    theme_id: str
    version: str
    display_name: str
    supported_schemes: tuple[ThemeScheme, ...]
    resource_budget: ResourceBudget
    compatibility: ThemeCompatibility = ThemeCompatibility()


@dataclass(frozen=True, slots=True)
class ThemeResource:
    logical_id: str
    relative_path: str
    media_type: str
    byte_size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    manifest: ThemeManifest
    scheme: ThemeScheme
    tokens: ThemeTokens
    resources: tuple[ThemeResource, ...] = ()
    fingerprint: str = ""

    @classmethod
    def create(
        cls,
        manifest: ThemeManifest,
        scheme: ThemeScheme,
        tokens: ThemeTokens,
        resources: tuple[ThemeResource, ...] = (),
    ) -> ThemeDefinition:
        definition = cls(manifest=manifest, scheme=scheme, tokens=tokens, resources=resources)
        return replace(definition, fingerprint=theme_fingerprint(definition))


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: ThemeErrorCode
    message: str
    location: str = "theme"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    status: RegistrationStatus
    provider_id: str | None = None
    theme_id: str | None = None
    error_code: ThemeErrorCode | None = None
    diagnostics: tuple[ValidationIssue, ...] = ()


class ThemeError(LookupError):
    def __init__(self, code: ThemeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class ThemeProvider(Protocol):
    def manifest(self) -> ThemeManifest: ...

    def load(self, theme_id: str, scheme: ThemeScheme) -> ThemeDefinition: ...


def theme_fingerprint(definition: ThemeDefinition) -> str:
    """Return a canonical identity independent of localized display text."""
    manifest = definition.manifest
    payload = {
        "manifest": {
            "schema_version": manifest.schema_version,
            "provider_id": manifest.provider_id,
            "theme_id": manifest.theme_id,
            "version": manifest.version,
            "supported_schemes": manifest.supported_schemes,
            "resource_budget": manifest.resource_budget,
            "compatibility": manifest.compatibility,
        },
        "scheme": definition.scheme,
        "tokens": definition.tokens,
        "resources": definition.resources,
    }
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _canonical_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value
