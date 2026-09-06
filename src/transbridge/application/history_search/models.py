"""Framework-neutral contracts for the persisted-history search projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import unicodedata


class HistoryEntryKind(StrEnum):
    TRANSLATION = "translation"
    TERM = "term"


class HistorySourceType(StrEnum):
    PROJECT_VARIANT = "project_variant"
    DICTIONARY = "dictionary"
    TERMINOLOGY = "terminology"


class HistorySearchScopeKind(StrEnum):
    PROJECT = "project"
    DICTIONARY = "dictionary"


def normalize_search_text(value: str) -> str:
    """Normalize only for matching; original text remains untouched for display."""

    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip().casefold()


@dataclass(frozen=True, slots=True)
class HistorySourceRef:
    source_type: HistorySourceType
    source_id: str
    label: str
    project_id: str | None = None
    project_name: str | None = None
    variant_id: str | None = None
    variant_name: str | None = None
    plugin_id: str | None = None
    dictionary_id: str | None = None
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.label.strip():
            raise ValueError("history source identity and label must not be empty")
        if len({key for key, _value in self.details}) != len(self.details):
            raise ValueError("history source details must have unique keys")
        object.__setattr__(self, "source_type", HistorySourceType(self.source_type))
        object.__setattr__(self, "details", tuple(sorted((str(k), str(v)) for k, v in self.details)))


@dataclass(frozen=True, slots=True)
class SourceRecord:
    kind: HistoryEntryKind
    original: str
    translation: str
    source: HistorySourceRef
    source_locale: str = ""
    target_locale: str = ""
    scope_key: str = ""
    status: str = ""

    def __post_init__(self) -> None:
        if not self.original.strip() or not self.translation.strip():
            raise ValueError("history records require non-empty original and translation")
        object.__setattr__(self, "kind", HistoryEntryKind(self.kind))
        object.__setattr__(self, "source_locale", self.source_locale.strip())
        object.__setattr__(self, "target_locale", self.target_locale.strip())
        object.__setattr__(self, "scope_key", self.scope_key.strip())

    @property
    def normalized_original(self) -> str:
        return normalize_search_text(self.original)

    @property
    def normalized_translation(self) -> str:
        return normalize_search_text(self.translation)


@dataclass(frozen=True, slots=True)
class HistoryDiagnostic:
    code: str
    message: str
    source: str = ""

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("history diagnostics require a code and message")


@dataclass(frozen=True, slots=True)
class HistorySearchScope:
    kind: HistorySearchScopeKind
    scope_id: str
    label: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", HistorySearchScopeKind(self.kind))
        if not self.scope_id.strip() or not self.label.strip():
            raise ValueError("history search scope identity and label must not be empty")


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    keyword: str
    kind: HistoryEntryKind | None = None
    limit: int = 200
    offset: int = 0
    scope: HistorySearchScope | None = None

    def __post_init__(self) -> None:
        if self.kind is not None:
            object.__setattr__(self, "kind", HistoryEntryKind(self.kind))
        if self.scope is not None and not isinstance(self.scope, HistorySearchScope):
            raise TypeError("history query scope must be a HistorySearchScope")
        if isinstance(self.limit, bool) or not 1 <= self.limit <= 500:
            raise ValueError("history query limit must be between 1 and 500")
        if isinstance(self.offset, bool) or self.offset < 0:
            raise ValueError("history query offset must be non-negative")


@dataclass(frozen=True, slots=True)
class HistorySearchHit:
    kind: HistoryEntryKind
    original: str
    translation: str
    source_locale: str
    target_locale: str
    scope_key: str
    status: str
    sources: tuple[HistorySourceRef, ...]
    has_alternatives: bool = False


@dataclass(frozen=True, slots=True)
class HistorySearchPage:
    items: tuple[HistorySearchHit, ...]
    total: int
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class IndexStatus:
    ready: bool
    record_count: int = 0
    built_at: str | None = None
    diagnostics: tuple[HistoryDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class RefreshReport:
    record_count: int
    provider_count: int
    built_at: str
    diagnostics: tuple[HistoryDiagnostic, ...] = ()
