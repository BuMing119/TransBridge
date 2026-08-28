from __future__ import annotations

from pathlib import Path
import sys
import tomllib

import pytest

from transbridge.config import paths as config_paths
from transbridge.config.language_profiles import (
    LanguageProfileError,
    discover_language_profiles,
    load_language_profile,
)

_REPO_PROMPTS = Path(__file__).resolve().parents[2] / "data" / "prompts"


def _write_profile(root: Path, locale: str, body: str) -> None:
    languages = root / "langs"
    languages.mkdir(parents=True, exist_ok=True)
    (languages / f"{locale}.toml").write_text(body, encoding="utf-8")


def test_load_language_profile_reads_metadata_and_optional_example(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        "ja_JP",
        '[lang]\nname = "日本語"\nsource = "English"\ntarget = "Japanese"\n'
        '\n[example]\nsource = "Hello"\ntarget = "こんにちは"\n',
    )

    profile = load_language_profile("ja_JP", prompts_dir=tmp_path)

    assert profile.locale == "ja_JP"
    assert profile.display_name == "日本語"
    assert profile.source_language == "English"
    assert profile.target_language == "Japanese"
    assert profile.example_target == "こんにちは"


@pytest.mark.parametrize("locale", ["", "../zh_CN", "zh/CN", ".", "zh_CN.toml"])
def test_load_language_profile_rejects_unsafe_locale(locale: str, tmp_path: Path) -> None:
    with pytest.raises(LanguageProfileError, match="Invalid language profile code"):
        load_language_profile(locale, prompts_dir=tmp_path)


def test_missing_profile_fails_without_language_fallback(tmp_path: Path) -> None:
    with pytest.raises(LanguageProfileError, match="Unsupported language profile 'ja_JP'"):
        load_language_profile("ja_JP", prompts_dir=tmp_path)


def test_incomplete_profile_fails(tmp_path: Path) -> None:
    _write_profile(tmp_path, "fr_FR", '[lang]\nsource = "English"\n')
    with pytest.raises(LanguageProfileError, match="lang.source and lang.target"):
        load_language_profile("fr_FR", prompts_dir=tmp_path)


def test_discovery_returns_valid_profiles_and_skips_invalid_files(tmp_path: Path) -> None:
    _write_profile(tmp_path, "ja_JP", '[lang]\nsource = "English"\ntarget = "Japanese"\n')
    _write_profile(tmp_path, "zh_CN", '[lang]\nsource = "English"\ntarget = "Simplified Chinese"\n')
    _write_profile(tmp_path, "fr_FR", '[lang]\nsource = "English"\n')

    with pytest.warns(UserWarning, match="Skipping unusable language profile fr_FR.toml"):
        profiles = discover_language_profiles(prompts_dir=tmp_path)

    assert [profile.locale for profile in profiles] == ["ja_JP", "zh_CN"]


def test_repository_language_file_contains_metadata_not_stage_prompts() -> None:
    with (_REPO_PROMPTS / "langs" / "zh_CN.toml").open("rb") as stream:
        data = tomllib.load(stream)

    assert data["lang"]["target"] == "Simplified Chinese"
    assert set(data) == {"lang", "example"}
    assert (_REPO_PROMPTS / "translation" / "default.toml").is_file()
    assert (_REPO_PROMPTS / "extraction" / "default.toml").is_file()


def test_data_resource_dir_uses_frozen_bundle_when_user_resource_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_data = tmp_path / "user-data"
    bundle_root = tmp_path / "bundle"
    bundled_prompts = bundle_root / "data" / "prompts"
    bundled_prompts.mkdir(parents=True)

    monkeypatch.setattr(config_paths, "get_data_dir", lambda: str(user_data))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert Path(config_paths.get_data_resource_dir("prompts")) == bundled_prompts
