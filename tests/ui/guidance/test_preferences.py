from __future__ import annotations

import os
from pathlib import Path

import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import GuidanceMode, UiPreferenceRepository


def _repository(tmp_path: Path, *, replace_func=os.replace) -> ConfigRepository:
    path = tmp_path / "transbridge.ini"
    return ConfigRepository(
        path,
        legacy_path=path,
        credential_store=UnavailableCredentialStore(),
        replace_func=replace_func,
    )


@pytest.mark.parametrize("mode", tuple(GuidanceMode))
def test_guidance_mode_round_trips_through_unified_repository(tmp_path: Path, mode: GuidanceMode) -> None:
    repository = _repository(tmp_path)
    preferences = UiPreferenceRepository(repository)

    result = preferences.save_guidance_mode(mode)
    loaded = UiPreferenceRepository(_repository(tmp_path)).load()

    assert result.saved
    assert result.snapshot is not None
    assert result.snapshot.guidance_mode is mode
    assert loaded.guidance_mode is mode
    assert loaded.config_revision == result.snapshot.config_revision
    assert repository.load().value("ui", "guidance_mode") == mode.value


def test_missing_and_invalid_values_fall_back_to_auto(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    missing = UiPreferenceRepository(repository).load()
    repository.update_sections({"ui": {"guidance_mode": "surprise"}})
    invalid = UiPreferenceRepository(repository).load()

    assert missing.guidance_mode is GuidanceMode.AUTO
    assert not missing.used_fallback
    assert invalid.guidance_mode is GuidanceMode.AUTO
    assert invalid.used_fallback
    assert invalid.diagnostic_code == "ui_guidance_mode_invalid"
    assert invalid.invalid_value == "surprise"


def test_write_failure_is_reported_and_preserves_verified_value(tmp_path: Path) -> None:
    healthy = _repository(tmp_path)
    UiPreferenceRepository(healthy).save_guidance_mode(GuidanceMode.GUIDED)
    before = healthy.path.read_bytes()

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError("injected preference failure")

    result = UiPreferenceRepository(_repository(tmp_path, replace_func=fail_replace)).save_guidance_mode(
        GuidanceMode.COMPACT
    )

    assert not result.saved
    assert result.snapshot is None
    assert result.diagnostic_code == "ui_guidance_mode_write_failed"
    assert "injected preference failure" in result.message
    assert healthy.path.read_bytes() == before
    assert UiPreferenceRepository(healthy).load().guidance_mode is GuidanceMode.GUIDED
