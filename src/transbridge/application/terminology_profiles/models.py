"""Immutable contracts for base-game localization terminology profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
import unicodedata


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _non_negative(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def logical_term_key(original: str, *, scope_kind: str = "project", plugin_id: str | None = None) -> str:
    normalized = normalize_term(_required(original, "term original"))
    scope = _required(scope_kind, "scope kind").lower()
    if scope not in {"project", "plugin"}:
        raise ValueError("scope kind must be project or plugin")
    plugin = None if plugin_id is None else _required(plugin_id, "plugin ID")
    if scope == "plugin" and plugin is None:
        raise ValueError("plugin scope requires a plugin ID")
    if scope == "project" and plugin is not None:
        raise ValueError("project scope cannot carry a plugin ID")
    return json.dumps([scope, plugin, normalized], ensure_ascii=False, separators=(",", ":"))


class ProfileState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, order=True, slots=True)
class ProfileTermMapping:
    original: str
    translation: str
    base_translation: str = ""
    scope_kind: str = "project"
    plugin_id: str | None = None
    term_key: str = field(init=False, compare=True)

    def __post_init__(self) -> None:
        original = _required(self.original, "term original")
        translation = _required(self.translation, "profile translation")
        scope_kind = _required(self.scope_kind, "scope kind").lower()
        plugin_id = None if self.plugin_id is None else _required(self.plugin_id, "plugin ID")
        key = logical_term_key(original, scope_kind=scope_kind, plugin_id=plugin_id)
        object.__setattr__(self, "original", original)
        object.__setattr__(self, "translation", translation)
        object.__setattr__(self, "base_translation", self.base_translation.strip())
        object.__setattr__(self, "scope_kind", scope_kind)
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "term_key", key)


@dataclass(frozen=True, order=True, slots=True)
class ProfileEntryOverride:
    entry_key: str
    translation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_key", _required(self.entry_key, "entry key"))
        object.__setattr__(self, "translation", _required(self.translation, "override translation"))


@dataclass(frozen=True, order=True, slots=True)
class ProfileOccurrenceBinding:
    entry_key: str
    term_key: str
    start: int
    end: int
    expected_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_key", _required(self.entry_key, "entry key"))
        object.__setattr__(self, "term_key", _required(self.term_key, "term key"))
        _non_negative(self.start, "binding start")
        _non_negative(self.end, "binding end")
        if self.end <= self.start:
            raise ValueError("binding end must be greater than start")
        expected = _required(self.expected_text, "binding expected text")
        if len(expected) != self.end - self.start:
            raise ValueError("binding span length must match expected text")
        object.__setattr__(self, "expected_text", expected)


@dataclass(frozen=True, slots=True)
class TerminologyProfileContent:
    mappings: tuple[ProfileTermMapping, ...] = ()
    overrides: tuple[ProfileEntryOverride, ...] = ()
    bindings: tuple[ProfileOccurrenceBinding, ...] = ()

    def __post_init__(self) -> None:
        mappings = tuple(sorted(self.mappings, key=lambda item: item.term_key))
        overrides = tuple(sorted(self.overrides, key=lambda item: item.entry_key))
        bindings = tuple(sorted(self.bindings, key=lambda item: (item.entry_key, item.start, item.end, item.term_key)))
        if len({item.term_key for item in mappings}) != len(mappings):
            raise ValueError("profile mappings must have unique logical term keys")
        if len({item.entry_key for item in overrides}) != len(overrides):
            raise ValueError("profile entry overrides must have unique entry keys")
        binding_ids = {(item.entry_key, item.start, item.end) for item in bindings}
        if len(binding_ids) != len(bindings):
            raise ValueError("profile bindings must not duplicate an entry span")
        object.__setattr__(self, "mappings", mappings)
        object.__setattr__(self, "overrides", overrides)
        object.__setattr__(self, "bindings", bindings)

    @property
    def content_digest(self) -> str:
        payload = {
            "bindings": [
                {
                    "end": item.end,
                    "entry_key": item.entry_key,
                    "expected_text": item.expected_text,
                    "start": item.start,
                    "term_key": item.term_key,
                }
                for item in self.bindings
            ],
            "mappings": [
                {
                    "base_translation": item.base_translation,
                    "original": item.original,
                    "plugin_id": item.plugin_id,
                    "scope_kind": item.scope_kind,
                    "translation": item.translation,
                }
                for item in self.mappings
            ],
            "overrides": [{"entry_key": item.entry_key, "translation": item.translation} for item in self.overrides],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(b"transbridge.terminology-profile.v1\0" + encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TerminologyProfile:
    profile_id: str
    project_id: str
    name: str
    state: ProfileState = ProfileState.ACTIVE
    draft_revision: int = 0
    draft: TerminologyProfileContent = TerminologyProfileContent()
    latest_published_revision: int | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        for name in ("profile_id", "project_id", "name"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "state", ProfileState(self.state))
        _non_negative(self.draft_revision, "draft revision")
        if self.latest_published_revision is not None:
            _non_negative(self.latest_published_revision, "published revision")


@dataclass(frozen=True, slots=True)
class PublishedTerminologyProfile:
    profile_id: str
    project_id: str
    revision: int
    name: str
    content_digest: str
    content: TerminologyProfileContent
    published_at: str

    def __post_init__(self) -> None:
        for name in ("profile_id", "project_id", "name", "content_digest", "published_at"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        _non_negative(self.revision, "published revision")
        if self.content_digest != self.content.content_digest:
            raise ValueError("published profile digest does not match its content")


@dataclass(frozen=True, slots=True)
class TerminologyProfileSelection:
    project_id: str
    variant_id: str
    profile_id: str
    revision: int
    selected_at: str

    def __post_init__(self) -> None:
        for name in ("project_id", "variant_id", "profile_id", "selected_at"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        _non_negative(self.revision, "selected revision")


__all__ = [
    "ProfileEntryOverride",
    "ProfileOccurrenceBinding",
    "ProfileState",
    "ProfileTermMapping",
    "PublishedTerminologyProfile",
    "TerminologyProfile",
    "TerminologyProfileContent",
    "TerminologyProfileSelection",
    "logical_term_key",
    "normalize_term",
]
