from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation import (
    DEFAULT_THEME_ID,
    NamedColor,
    RegistrationStatus,
    ResourceBudget,
    RgbaColor,
    ThemeDefinition,
    ThemeErrorCode,
    ThemeManifest,
    ThemeRegistry,
    ThemeResource,
    ThemeScheme,
    builtin_providers,
    register_builtin_themes,
)
from transbridge.ui.foundation.adapters import DomainBrushes
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService

ACME_PROVIDER_ID = "acme.test-provider"
ACME_THEME_ID = "acme.test-theme"


@dataclass(slots=True)
class AcmeProvider:
    _manifest: ThemeManifest
    _definitions: tuple[ThemeDefinition, ...]
    fail_scheme: ThemeScheme | None = None

    def manifest(self) -> ThemeManifest:
        return self._manifest

    def load(self, theme_id: str, scheme: ThemeScheme) -> ThemeDefinition:
        if self.fail_scheme is scheme:
            raise RuntimeError("provider-private-path-must-not-escape")
        if theme_id != self._manifest.theme_id:
            raise KeyError(theme_id)
        return next(definition for definition in self._definitions if definition.scheme is scheme)


def _preferences(tmp_path: Path) -> UiPreferenceRepository:
    path = tmp_path / "provider-contract.ini"
    return UiPreferenceRepository(
        ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    )


def _replace_named_color(tokens, name: str, value: str):
    colors = tuple(
        NamedColor(item.name, RgbaColor.from_hex(value)) if item.name == name else item
        for item in tokens.primitives.colors
    )
    return replace(tokens, primitives=replace(tokens.primitives, colors=colors))


def _acme_provider() -> AcmeProvider:
    source = builtin_providers()[0]
    source_manifest = source.manifest()
    manifest = replace(
        source_manifest,
        provider_id=ACME_PROVIDER_ID,
        theme_id=ACME_THEME_ID,
        version="1.0.0",
        display_name="Acme Contract Theme",
        resource_budget=ResourceBudget(max_resources=2, max_item_bytes=1024, max_total_bytes=2048),
    )
    resource = ThemeResource("icon.acme", "icons/acme.svg", "image/svg+xml", 128, "0" * 64)
    definitions = []
    for scheme in manifest.supported_schemes:
        tokens = source.load(source_manifest.theme_id, scheme).tokens
        accent = "#6A00A8" if scheme is ThemeScheme.LIGHT else "#FFB3F8"
        tokens = _replace_named_color(tokens, "focus", accent)
        tokens = _replace_named_color(tokens, "info", accent)
        definitions.append(ThemeDefinition.create(manifest, scheme, tokens, (resource,)))
    return AcmeProvider(manifest, tuple(definitions))


def _registry_identity(registry: ThemeRegistry) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (definition.manifest.provider_id, definition.scheme.value, definition.fingerprint)
        for definition in registry.themes
    )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_third_party_provider_flows_registry_preview_apply_components_and_fallback(qapp, tmp_path: Path) -> None:
    registry = ThemeRegistry()
    provider = _acme_provider()
    registered = registry.register(provider)
    assert registered.status is RegistrationStatus.REGISTERED
    assert {registry.resolve(ACME_THEME_ID, scheme).scheme for scheme in ThemeScheme} == set(ThemeScheme)
    assert all(result.status is RegistrationStatus.REGISTERED for result in register_builtin_themes(registry))

    preferences = _preferences(tmp_path)
    assert preferences.save_theme_preference(ThemeMode.LIGHT, ACME_THEME_ID).saved
    service = ThemeService(qapp, registry, preferences)
    started = service.start()
    assert started.snapshot is not None
    assert started.snapshot.provider_id == ACME_PROVIDER_ID
    assert DomainBrushes(started.snapshot).task("failed").label_key == "task.failed"

    before_preview = service.snapshot()
    preview = service.preview(ThemePreference(ThemeMode.DARK, ACME_THEME_ID))
    assert preview.effective_scheme is ThemeScheme.DARK
    assert service.snapshot() is before_preview
    applied = service.set_preference(ThemePreference(ThemeMode.DARK, ACME_THEME_ID), persist=False)
    assert applied.status is ThemeApplyStatus.APPLIED
    assert applied.snapshot is not None and applied.snapshot.provider_id == ACME_PROVIDER_ID

    registry.unregister(ACME_PROVIDER_ID)
    fallback = service.set_preference(ThemePreference(ThemeMode.LIGHT, ACME_THEME_ID), persist=False)
    assert fallback.status is ThemeApplyStatus.FALLBACK
    assert fallback.snapshot is not None and fallback.snapshot.theme_id == DEFAULT_THEME_ID
    assert fallback.diagnostics[-1] == "theme_fallback_builtin"
    service.close()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("forward-schema", ThemeErrorCode.SCHEMA_UNSUPPORTED),
        ("missing-token", ThemeErrorCode.TOKENS_INVALID),
        ("resource-budget", ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED),
        ("resource-path", ThemeErrorCode.TOKENS_INVALID),
        ("provider-exception", ThemeErrorCode.PROVIDER_FAILED),
    ],
)
def test_single_fault_providers_are_rejected_atomically(mutation: str, expected: ThemeErrorCode) -> None:
    registry = ThemeRegistry()
    assert all(result.status is RegistrationStatus.REGISTERED for result in register_builtin_themes(registry))
    before = _registry_identity(registry)
    provider = _acme_provider()

    if mutation == "forward-schema":
        manifest = replace(provider.manifest(), schema_version=provider.manifest().schema_version + 1)
        definitions = tuple(
            ThemeDefinition.create(manifest, definition.scheme, definition.tokens, definition.resources)
            for definition in provider._definitions
        )
        provider = AcmeProvider(manifest, definitions)
    elif mutation == "missing-token":
        definitions = []
        for definition in provider._definitions:
            domain = replace(
                definition.tokens.domain,
                task=tuple(style for style in definition.tokens.domain.task if style.key != "failed"),
            )
            tokens = replace(definition.tokens, domain=domain)
            definitions.append(
                ThemeDefinition.create(provider.manifest(), definition.scheme, tokens, definition.resources)
            )
        provider = AcmeProvider(provider.manifest(), tuple(definitions))
    elif mutation == "resource-budget":
        manifest = replace(
            provider.manifest(),
            resource_budget=ResourceBudget(max_resources=1, max_item_bytes=32, max_total_bytes=32),
        )
        definitions = tuple(
            ThemeDefinition.create(manifest, definition.scheme, definition.tokens, definition.resources)
            for definition in provider._definitions
        )
        provider = AcmeProvider(manifest, definitions)
    elif mutation == "resource-path":
        unsafe = ThemeResource("icon.acme", "../escape.svg", "image/svg+xml", 16, "0" * 64)
        definitions = tuple(
            ThemeDefinition.create(provider.manifest(), definition.scheme, definition.tokens, (unsafe,))
            for definition in provider._definitions
        )
        provider = AcmeProvider(provider.manifest(), definitions)
    elif mutation == "provider-exception":
        provider.fail_scheme = ThemeScheme.DARK

    rejected = registry.register(provider)
    assert rejected.status is RegistrationStatus.REJECTED
    assert rejected.error_code is expected
    assert _registry_identity(registry) == before
    assert all("private-path" not in issue.message for issue in rejected.diagnostics)


def test_provider_id_or_theme_id_conflict_does_not_replace_registered_content() -> None:
    registry = ThemeRegistry()
    original = _acme_provider()
    assert registry.register(original).status is RegistrationStatus.REGISTERED
    before = _registry_identity(registry)
    conflicting = _acme_provider()
    manifest = replace(conflicting.manifest(), provider_id="other.test-provider")
    definitions = tuple(
        ThemeDefinition.create(manifest, definition.scheme, definition.tokens, definition.resources)
        for definition in conflicting._definitions
    )

    rejected = registry.register(AcmeProvider(manifest, definitions))

    assert rejected.status is RegistrationStatus.REJECTED
    assert rejected.error_code is ThemeErrorCode.ID_CONFLICT
    assert _registry_identity(registry) == before
