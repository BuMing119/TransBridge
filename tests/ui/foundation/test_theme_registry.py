from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import subprocess
import sys

import pytest

from transbridge.ui.foundation import (
    ColorReference,
    NamedColor,
    RegistrationStatus,
    ResourceBudget,
    ThemeDefinition,
    ThemeError,
    ThemeErrorCode,
    ThemeManifest,
    ThemeRegistry,
    ThemeResource,
    ThemeScheme,
    builtin_providers,
    theme_fingerprint,
)


class Provider:
    def __init__(self, manifest: ThemeManifest, definitions: tuple[ThemeDefinition, ...]) -> None:
        self._manifest = manifest
        self._definitions = definitions
        self.loads = 0

    def manifest(self) -> ThemeManifest:
        return self._manifest

    def load(self, theme_id: str, scheme: ThemeScheme) -> ThemeDefinition:
        self.loads += 1
        return next(item for item in self._definitions if item.scheme is scheme)


def _provider(
    *,
    provider_id: str = "example.provider",
    theme_id: str = "example.theme",
    schemes: tuple[ThemeScheme, ...] = (ThemeScheme.LIGHT, ThemeScheme.DARK),
) -> Provider:
    source = builtin_providers()[0]
    source_manifest = source.manifest()
    manifest = replace(
        source_manifest,
        provider_id=provider_id,
        theme_id=theme_id,
        display_name="Example Theme",
        supported_schemes=schemes,
    )
    definitions = tuple(
        ThemeDefinition.create(
            manifest,
            scheme,
            source.load(source_manifest.theme_id, scheme).tokens,
        )
        for scheme in schemes
    )
    return Provider(manifest, definitions)


def _with_definition(provider: Provider, definition: ThemeDefinition) -> Provider:
    definitions = tuple(definition if item.scheme is definition.scheme else item for item in provider._definitions)
    return Provider(provider.manifest(), definitions)


def test_foundation_import_graph_is_qt_free() -> None:
    source_root = Path(__file__).resolve().parents[3] / "src"
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(source_root)!r}); "
        "import transbridge.ui.foundation; "
        "assert not any(name == 'PyQt6' or name.startswith('PyQt6.') for name in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr


def test_contracts_are_frozen_slotted_and_fingerprint_is_canonical() -> None:
    provider = _provider(schemes=(ThemeScheme.LIGHT,))
    definition = provider._definitions[0]
    with pytest.raises(FrozenInstanceError):
        definition.scheme = ThemeScheme.DARK  # type: ignore[misc]
    assert not hasattr(definition, "__dict__")

    renamed_manifest = replace(definition.manifest, display_name="Localized Name")
    renamed = replace(definition, manifest=renamed_manifest, fingerprint="")
    assert theme_fingerprint(renamed) == definition.fingerprint
    assert (
        ThemeDefinition.create(
            definition.manifest,
            definition.scheme,
            definition.tokens,
            definition.resources,
        ).fingerprint
        == definition.fingerprint
    )


def test_registry_normalizes_color_references_and_is_idempotent() -> None:
    registry = ThemeRegistry()
    provider = _provider()

    first = registry.register(provider)
    second = registry.register(provider)

    assert first.status is RegistrationStatus.REGISTERED
    assert second.status is RegistrationStatus.UNCHANGED
    assert provider.loads == 4
    resolved = registry.resolve("example.theme", ThemeScheme.LIGHT)
    assert resolved.tokens.semantic.window.canonical == "#F6F8FBFF"
    assert all(
        isinstance(token.value, object) and not isinstance(token.value, ColorReference)
        for token in resolved.tokens.primitives.colors
    )


def test_unregister_removes_all_provider_schemes_without_affecting_others() -> None:
    registry = ThemeRegistry()
    first = _provider()
    second = _provider(provider_id="other.provider", theme_id="other.theme", schemes=(ThemeScheme.LIGHT,))
    registry.register(first)
    registry.register(second)

    result = registry.unregister("example.provider")

    assert result.status is RegistrationStatus.REGISTERED
    with pytest.raises(ThemeError) as exc_info:
        registry.resolve("example.theme", ThemeScheme.LIGHT)
    assert exc_info.value.code is ThemeErrorCode.UNKNOWN
    assert registry.resolve("other.theme", ThemeScheme.LIGHT).manifest.provider_id == "other.provider"
    assert registry.unregister("missing.provider").status is RegistrationStatus.UNCHANGED


@pytest.mark.parametrize(
    ("manifest_change", "expected"),
    [
        ({"schema_version": 2}, ThemeErrorCode.SCHEMA_UNSUPPORTED),
        ({"schema_version": -1}, ThemeErrorCode.SCHEMA_UNSUPPORTED),
        ({"provider_id": "Invalid ID"}, ThemeErrorCode.TOKENS_INVALID),
        ({"theme_id": "../escape"}, ThemeErrorCode.TOKENS_INVALID),
    ],
)
def test_registry_rejects_unsupported_schema_and_illegal_ids_atomically(
    manifest_change: dict[str, object], expected: ThemeErrorCode
) -> None:
    registry = ThemeRegistry()
    provider = _provider()
    manifest = replace(provider.manifest(), **manifest_change)
    definitions = tuple(ThemeDefinition.create(manifest, item.scheme, item.tokens) for item in provider._definitions)

    result = registry.register(Provider(manifest, definitions))

    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is expected
    assert registry.themes == ()


def test_registry_rejects_missing_domain_state_and_invalid_number() -> None:
    provider = _provider(schemes=(ThemeScheme.LIGHT,))
    definition = provider._definitions[0]
    missing_domain = replace(
        definition.tokens.domain,
        task=tuple(style for style in definition.tokens.domain.task if style.key != "failed"),
    )
    missing_tokens = replace(definition.tokens, domain=missing_domain)
    missing_definition = ThemeDefinition.create(definition.manifest, definition.scheme, missing_tokens)

    result = ThemeRegistry().register(_with_definition(provider, missing_definition))

    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is ThemeErrorCode.TOKENS_INVALID
    assert any("missing required states" in issue.message for issue in result.diagnostics)

    invalid_spacing = replace(definition.tokens.primitives.spacing, md=float("nan"))
    invalid_primitives = replace(definition.tokens.primitives, spacing=invalid_spacing)
    invalid_tokens = replace(definition.tokens, primitives=invalid_primitives)
    invalid_definition = replace(definition, tokens=invalid_tokens, fingerprint="")
    result = ThemeRegistry().register(_with_definition(provider, invalid_definition))
    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is ThemeErrorCode.TOKENS_INVALID


def test_registry_rejects_recursive_and_unresolved_color_references() -> None:
    provider = _provider(schemes=(ThemeScheme.LIGHT,))
    definition = provider._definitions[0]
    colors = (
        NamedColor("first", ColorReference("second")),
        NamedColor("second", ColorReference("first")),
    )
    primitives = replace(definition.tokens.primitives, colors=colors)
    tokens = replace(definition.tokens, primitives=primitives)
    recursive = replace(definition, tokens=tokens, fingerprint="")

    result = ThemeRegistry().register(_with_definition(provider, recursive))

    assert result.status is RegistrationStatus.REJECTED
    assert any("recursive" in issue.message or "unresolved" in issue.message for issue in result.diagnostics)
    assert ThemeRegistry().themes == ()


@pytest.mark.parametrize(
    "relative_path", ["../secret.png", "/absolute/icon.png", "https://example.test/icon.png", "dir\\icon.png"]
)
def test_registry_rejects_unsafe_resource_paths(relative_path: str) -> None:
    provider = _provider(schemes=(ThemeScheme.LIGHT,))
    definition = provider._definitions[0]
    resource = ThemeResource("icon.sample", relative_path, "image/png", 128, "0" * 64)
    invalid = ThemeDefinition.create(definition.manifest, definition.scheme, definition.tokens, (resource,))

    result = ThemeRegistry().register(_with_definition(provider, invalid))

    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is ThemeErrorCode.TOKENS_INVALID


def test_registry_rejects_resource_budget_overrun() -> None:
    provider = _provider(schemes=(ThemeScheme.LIGHT,))
    definition = provider._definitions[0]
    budget = ResourceBudget(max_resources=1, max_item_bytes=64, max_total_bytes=64)
    manifest = replace(definition.manifest, resource_budget=budget)
    resource = ThemeResource("icon.sample", "icons/sample.png", "image/png", 65, "0" * 64)
    invalid = ThemeDefinition.create(manifest, definition.scheme, definition.tokens, (resource,))

    result = ThemeRegistry().register(Provider(manifest, (invalid,)))

    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is ThemeErrorCode.RESOURCE_BUDGET_EXCEEDED


def test_provider_exception_and_partial_scheme_failure_do_not_pollute_registry() -> None:
    class BrokenProvider(Provider):
        def load(self, theme_id: str, scheme: ThemeScheme) -> ThemeDefinition:
            if scheme is ThemeScheme.DARK:
                raise RuntimeError("sensitive path C:/private/theme")
            return super().load(theme_id, scheme)

    source = _provider()
    broken = BrokenProvider(source.manifest(), source._definitions)
    registry = ThemeRegistry()

    result = registry.register(broken)

    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is ThemeErrorCode.PROVIDER_FAILED
    assert registry.themes == ()
    assert all("private" not in issue.message for issue in result.diagnostics)


def test_registry_rejects_theme_id_conflict_without_replacing_original() -> None:
    registry = ThemeRegistry()
    original = _provider()
    assert registry.register(original).status is RegistrationStatus.REGISTERED
    changed = _provider(provider_id="other.provider")
    definition = changed._definitions[0]
    colors = definition.tokens.primitives.colors + (NamedColor("extra", definition.tokens.semantic.error),)
    changed_tokens = replace(definition.tokens, primitives=replace(definition.tokens.primitives, colors=colors))
    changed_definition = ThemeDefinition.create(changed.manifest(), definition.scheme, changed_tokens)
    changed = _with_definition(changed, changed_definition)

    result = registry.register(changed)

    assert result.status is RegistrationStatus.REJECTED
    assert result.error_code is ThemeErrorCode.ID_CONFLICT
    assert registry.resolve("example.theme", ThemeScheme.LIGHT).manifest.provider_id == "example.provider"
