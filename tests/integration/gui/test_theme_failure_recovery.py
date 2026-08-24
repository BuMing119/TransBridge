from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository, ConfigSnapshot
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation import (
    RegistrationStatus,
    ThemeDefinition,
    ThemeErrorCode,
    ThemeScheme,
    builtin_providers,
    create_builtin_registry,
)
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService


@dataclass(frozen=True, slots=True)
class _BusinessSideEffects:
    application_commands: int = 0
    task_run_ids: int = 0
    network_requests: int = 0
    file_writes: int = 0
    preflight_runs: int = 0
    confirm_tokens: int = 0


class _ForwardProvider:
    def __init__(self) -> None:
        source = builtin_providers()[0]
        source_manifest = source.manifest()
        self._manifest = replace(
            source_manifest,
            schema_version=source_manifest.schema_version + 1,
            provider_id="failure.test-provider",
            theme_id="failure.test-theme",
        )
        self._definitions = tuple(
            ThemeDefinition.create(
                self._manifest,
                scheme,
                source.load(source_manifest.theme_id, scheme).tokens,
            )
            for scheme in self._manifest.supported_schemes
        )

    def manifest(self):
        return self._manifest

    def load(self, _theme_id, scheme: ThemeScheme):
        return next(definition for definition in self._definitions if definition.scheme is scheme)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _repositories(tmp_path: Path) -> tuple[ConfigRepository, UiPreferenceRepository]:
    path = tmp_path / "failure-recovery.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    repository.update_sections({
        "llm": {"provider": "openai-compatible", "base_url": "https://example.invalid", "model": "m-test"},
        "paratranz": {"project_id": "42"},
        "guardrails": {"max_input_size": "2048"},
    })
    return repository, UiPreferenceRepository(repository)


def _other_sections(snapshot: ConfigSnapshot) -> tuple[object, ...]:
    return tuple(section for section in snapshot.sections if section.name != "ui")


def _palette_colors(palette: QPalette) -> tuple[int, ...]:
    roles = (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.Button,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.HighlightedText,
    )
    groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled)
    return tuple(palette.color(group, role).rgba() for group in groups for role in roles)


def test_invalid_provider_rejection_preserves_palette_revision_config_and_business_counters(
    qapp, tmp_path: Path
) -> None:
    repository, preferences = _repositories(tmp_path)
    assert preferences.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID).saved
    registry = create_builtin_registry()
    service = ThemeService(qapp, registry, preferences)
    assert service.start().snapshot is not None
    before_theme = service.snapshot()
    before_palette = _palette_colors(qapp.palette())
    before_config = repository.load()
    before_effects = _BusinessSideEffects()

    rejected = registry.register(_ForwardProvider())

    assert rejected.status is RegistrationStatus.REJECTED
    assert rejected.error_code is ThemeErrorCode.SCHEMA_UNSUPPORTED
    assert service.snapshot() is before_theme
    assert _palette_colors(qapp.palette()) == before_palette
    assert repository.load() == before_config
    assert _BusinessSideEffects() == before_effects
    service.close()


def test_qt_apply_failure_restores_last_good_dark_without_signal_persist_or_side_effect(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    repository, preferences = _repositories(tmp_path)
    assert preferences.save_theme_preference(ThemeMode.DARK, DEFAULT_THEME_ID).saved
    service = ThemeService(qapp, create_builtin_registry(), preferences)
    assert service.start().snapshot is not None
    before_theme = service.snapshot()
    before_palette = _palette_colors(qapp.palette())
    before_config = repository.load()
    before_effects = _BusinessSideEffects()
    emitted: list[int] = []
    service.theme_changed.connect(lambda revision, _snapshot: emitted.append(revision))
    real_set_palette = qapp.setPalette
    calls = 0

    def fail_candidate_once(palette: QPalette) -> None:
        nonlocal calls
        calls += 1
        real_set_palette(palette)
        if calls == 1:
            raise RuntimeError("injected Qt palette failure")

    monkeypatch.setattr(qapp, "setPalette", fail_candidate_once)
    result = service.set_preference(ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID), persist=True)

    assert result.status is ThemeApplyStatus.FAILED
    assert result.snapshot is before_theme and service.snapshot() is before_theme
    assert _palette_colors(qapp.palette()) == before_palette
    assert emitted == []
    assert calls == 2
    assert repository.load() == before_config
    assert _BusinessSideEffects() == before_effects
    service.close()


def test_missing_persisted_provider_falls_back_without_damaging_other_config_and_builtin_remains_selectable(
    qapp, tmp_path: Path
) -> None:
    repository, preferences = _repositories(tmp_path)
    assert preferences.save_theme_preference(ThemeMode.DARK, "removed.external-theme").saved
    persisted = repository.load()
    other_sections = _other_sections(persisted)
    before_effects = _BusinessSideEffects()

    service = ThemeService(qapp, create_builtin_registry(), preferences)
    started = service.start()

    assert started.status is ThemeApplyStatus.FALLBACK
    assert started.snapshot is not None
    assert started.snapshot.theme_id == DEFAULT_THEME_ID
    assert started.snapshot.effective_scheme is ThemeScheme.DARK
    assert "theme_fallback_builtin" in started.diagnostics
    assert repository.load() == persisted
    assert _other_sections(repository.load()) == other_sections
    assert _BusinessSideEffects() == before_effects

    selected = service.set_preference(ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID), persist=True)
    committed = repository.load()
    assert selected.status is ThemeApplyStatus.APPLIED
    assert selected.persisted
    assert committed.revision == persisted.revision + 1
    assert _other_sections(committed) == other_sections
    assert preferences.load().theme_id == DEFAULT_THEME_ID
    assert _BusinessSideEffects() == before_effects
    service.close()
