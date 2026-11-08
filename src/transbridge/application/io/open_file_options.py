"""Immutable user choices for opening sources and optional translation import."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenFileOptions:
    source_paths: tuple[str, ...] = ()
    format_hint: str | None = None
    translation_path: str | None = None
    translation_format_hint: str | None = None
    replace_existing: bool = False
    strings_lang: str = "chinese"
    skip_empty: bool = True
