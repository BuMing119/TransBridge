# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Supported game text encodings and application defaults."""

from __future__ import annotations

import codecs
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .repository import ConfigRepository

DEFAULT_TEXT_ENCODING = "utf-8"
DEFAULT_SOURCE_LANGUAGE = "english"
DEFAULT_TARGET_LANGUAGE = "chinese"
TEXT_INPUT_ENCODING = "text_input_encoding"
TEXT_OUTPUT_ENCODING = "text_output_encoding"
TEXT_SOURCE_LANGUAGE = "text_source_language"
TEXT_TARGET_LANGUAGE = "text_target_language"
XTRANSLATOR_READ_POLICY = "xtranslator-v1"
LEGACY_READ_POLICY = "legacy-v1"
STRICT_READ_POLICY = "strict-v1"
_READ_POLICIES = frozenset({XTRANSLATOR_READ_POLICY, LEGACY_READ_POLICY, STRICT_READ_POLICY})

# xTranslator Data/SkyrimSE/codepage.txt specifies UTF-8 for all rows,
# with at most one language-specific fallback.
_LANGUAGES = {
    "english": ("英语", "cp1252"),
    "chinese": ("中文", None),
    "french": ("法语", "cp1252"),
    "german": ("德语", "cp1252"),
    "italian": ("意大利语", "cp1252"),
    "spanish": ("西班牙语", "cp1252"),
    "portuguese": ("葡萄牙语", "cp1252"),
    "danish": ("丹麦语", "cp1252"),
    "finnish": ("芬兰语", "cp1252"),
    "norwegian": ("挪威语", "cp1252"),
    "swedish": ("瑞典语", "cp1252"),
    "polish": ("波兰语", "cp1250"),
    "czech": ("捷克语", "cp1250"),
    "hungarian": ("匈牙利语", "cp1250"),
    "russian": ("俄语", "cp1251"),
    "greek": ("希腊语", "cp1253"),
    "turkish": ("土耳其语", "cp1254"),
    "arabic": ("阿拉伯语", "cp1256"),
    "japanese": ("日语", "cp932"),
    "korean": ("韩语", None),
    "thai": ("泰语", None),
}
TEXT_LANGUAGES = tuple((language, label) for language, (label, _) in _LANGUAGES.items())
TEXT_ENCODINGS = (
    ("utf-8", "UTF-8"),
    ("cp1250", "Windows-1250 (Central European)"),
    ("cp1251", "Windows-1251 (Cyrillic)"),
    ("cp1252", "Windows-1252 (Western European)"),
    ("cp1253", "Windows-1253 (Greek)"),
    ("cp1254", "Windows-1254 (Turkish)"),
    ("cp1255", "Windows-1255 (Hebrew)"),
    ("cp1256", "Windows-1256 (Arabic)"),
    ("cp1257", "Windows-1257 (Baltic)"),
    ("cp1258", "Windows-1258 (Vietnamese)"),
    ("cp874", "Windows-874 (Thai)"),
    ("cp932", "Windows-932 (Japanese)"),
    ("cp936", "Windows-936 (Simplified Chinese)"),
    ("cp949", "Windows-949 (Korean)"),
    ("cp950", "Windows-950 (Traditional Chinese)"),
)
_SUPPORTED = frozenset(codec for codec, _ in TEXT_ENCODINGS)
_CODEC_NAMES = {"gbk": "cp936"}
_SECTION = "text_encoding"
_DEFAULT_INPUT = "default_text_input_encoding"
_DEFAULT_OUTPUT = "default_text_output_encoding"
_DEFAULT_SOURCE = "default_text_source_language"
_DEFAULT_TARGET = "default_text_target_language"


def normalize_text_encoding(value: str) -> str:
    """Normalize supported aliases while rejecting codecs outside the game range."""
    if not isinstance(value, str):
        raise ValueError("A supported game text encoding is required.")
    alias = value.strip().lower().replace("_", "-")
    if alias.startswith("windows-"):
        alias = "cp" + alias.removeprefix("windows-")
    if alias.isdecimal():
        alias = "cp" + alias
    try:
        name = codecs.lookup(alias).name
    except LookupError as exc:
        raise ValueError(f"Unsupported game text encoding: {value!r}.") from exc
    normalized = _CODEC_NAMES.get(name, name)
    if normalized not in _SUPPORTED:
        raise ValueError(f"Unsupported game text encoding: {value!r}.")
    return normalized


def normalize_text_language(value: str) -> str:
    """Normalize a Skyrim SE language label used by the game text policy."""
    if not isinstance(value, str) or (name := value.strip().casefold()) not in _LANGUAGES:
        raise ValueError(f"Unsupported game text language: {value!r}.")
    return name


def fallback_encoding(source_language: str) -> str | None:
    return _LANGUAGES[normalize_text_language(source_language)][1]


def language_from_suffix(suffix: str | None) -> str:
    """Retain compatibility with arbitrary Strings filename suffixes."""
    language = suffix.casefold() if isinstance(suffix, str) else ""
    return language if language in _LANGUAGES else DEFAULT_SOURCE_LANGUAGE


@dataclass(frozen=True, slots=True)
class TextReadOptions:
    input_encoding: str = DEFAULT_TEXT_ENCODING
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    text_read_policy: str = XTRANSLATOR_READ_POLICY

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_encoding", normalize_text_encoding(self.input_encoding))
        object.__setattr__(self, "source_language", normalize_text_language(self.source_language))
        if not isinstance(self.text_read_policy, str) or self.text_read_policy not in _READ_POLICIES:
            raise ValueError(f"Unsupported game text read policy: {self.text_read_policy!r}.")

    def as_options(self) -> dict[str, str]:
        return {
            "input_encoding": self.input_encoding,
            "source_language": self.source_language,
            "text_read_policy": self.text_read_policy,
        }


@dataclass(frozen=True, slots=True)
class TextEncodings:
    input_encoding: str = DEFAULT_TEXT_ENCODING
    output_encoding: str = DEFAULT_TEXT_ENCODING
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    target_language: str = DEFAULT_TARGET_LANGUAGE

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_encoding", normalize_text_encoding(self.input_encoding))
        object.__setattr__(self, "output_encoding", normalize_text_encoding(self.output_encoding))
        object.__setattr__(self, "source_language", normalize_text_language(self.source_language))
        object.__setattr__(self, "target_language", normalize_text_language(self.target_language))

    @classmethod
    def from_project_data(cls, data: Mapping[str, str]) -> TextEncodings:
        return cls(
            data.get(TEXT_INPUT_ENCODING, DEFAULT_TEXT_ENCODING),
            data.get(TEXT_OUTPUT_ENCODING, DEFAULT_TEXT_ENCODING),
            data.get(TEXT_SOURCE_LANGUAGE, DEFAULT_SOURCE_LANGUAGE),
            data.get(TEXT_TARGET_LANGUAGE, DEFAULT_TARGET_LANGUAGE),
        )

    def to_project_data(self) -> dict[str, str]:
        return {
            TEXT_INPUT_ENCODING: self.input_encoding,
            TEXT_OUTPUT_ENCODING: self.output_encoding,
            TEXT_SOURCE_LANGUAGE: self.source_language,
            TEXT_TARGET_LANGUAGE: self.target_language,
        }

    def read_options(self) -> dict[str, str]:
        return TextReadOptions(self.input_encoding, self.source_language).as_options()


class TextEncodingDefaults:
    def __init__(self, repository: ConfigRepository | None = None) -> None:
        if repository is None:
            from .repository import default_config_repository

            repository = default_config_repository()
        self._repository = repository

    def load(self) -> TextEncodings:
        values = dict(self._repository.load().section(_SECTION).values)
        return TextEncodings(
            values.get(_DEFAULT_INPUT, DEFAULT_TEXT_ENCODING),
            values.get(_DEFAULT_OUTPUT, DEFAULT_TEXT_ENCODING),
            values.get(_DEFAULT_SOURCE, DEFAULT_SOURCE_LANGUAGE),
            values.get(_DEFAULT_TARGET, DEFAULT_TARGET_LANGUAGE),
        )

    def save(self, encodings: TextEncodings) -> None:
        self._repository.update_sections({
            _SECTION: {
                _DEFAULT_INPUT: encodings.input_encoding,
                _DEFAULT_OUTPUT: encodings.output_encoding,
                _DEFAULT_SOURCE: encodings.source_language,
                _DEFAULT_TARGET: encodings.target_language,
            }
        })
