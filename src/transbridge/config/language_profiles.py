"""Strict loading and discovery for prompt language profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
import warnings

from .paths import get_data_resource_dir

_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{2,8})*$")


class LanguageProfileError(ValueError):
    """Raised when a requested language profile cannot be used safely."""


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """Model-facing language metadata selected by a stable locale code."""

    locale: str
    display_name: str
    source_language: str
    target_language: str
    example_source: str | None = None
    example_target: str | None = None


def get_prompts_dir() -> Path:
    """Return the shared prompt-data directory."""

    return Path(get_data_resource_dir("prompts"))


def _validate_locale(locale: str) -> str:
    normalized = str(locale or "").strip()
    if not _LOCALE_PATTERN.fullmatch(normalized):
        raise LanguageProfileError(
            f"Invalid language profile code {locale!r}; use a locale such as 'zh_CN' or 'ja_JP'."
        )
    return normalized


def load_language_profile(locale: str, *, prompts_dir: Path | None = None) -> LanguageProfile:
    """Load one locale profile, failing instead of guessing a target language."""

    normalized = _validate_locale(locale)
    root = Path(prompts_dir) if prompts_dir is not None else get_prompts_dir()
    path = root / "langs" / f"{normalized}.toml"
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise LanguageProfileError(f"Unsupported language profile {normalized!r}: {path} does not exist.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LanguageProfileError(f"Invalid TOML in language profile {normalized!r}: {exc}") from exc
    except OSError as exc:
        raise LanguageProfileError(f"Cannot read language profile {normalized!r}: {exc}") from exc

    lang = data.get("lang")
    if not isinstance(lang, dict):
        raise LanguageProfileError(f"Language profile {normalized!r} must define a [lang] table.")
    source = str(lang.get("source") or "").strip()
    target = str(lang.get("target") or "").strip()
    if not source or not target:
        raise LanguageProfileError(
            f"Language profile {normalized!r} must define non-empty lang.source and lang.target values."
        )

    example = data.get("example")
    if not isinstance(example, dict):
        example = {}
    example_source = str(example.get("source") or "").strip() or None
    example_target = str(example.get("target") or "").strip() or None
    if (example_source is None) != (example_target is None):
        raise LanguageProfileError(
            f"Language profile {normalized!r} must define both example.source and example.target, or neither."
        )

    return LanguageProfile(
        locale=normalized,
        display_name=str(lang.get("name") or target).strip(),
        source_language=source,
        target_language=target,
        example_source=example_source,
        example_target=example_target,
    )


def discover_language_profiles(*, prompts_dir: Path | None = None) -> tuple[LanguageProfile, ...]:
    """Return valid installed profiles in locale order, warning about broken files."""

    root = Path(prompts_dir) if prompts_dir is not None else get_prompts_dir()
    languages_dir = root / "langs"
    if not languages_dir.is_dir():
        return ()

    profiles: list[LanguageProfile] = []
    for path in sorted(languages_dir.glob("*.toml"), key=lambda item: item.stem.casefold()):
        try:
            profiles.append(load_language_profile(path.stem, prompts_dir=root))
        except LanguageProfileError as exc:
            warnings.warn(f"Skipping unusable language profile {path.name}: {exc}")
    return tuple(profiles)
