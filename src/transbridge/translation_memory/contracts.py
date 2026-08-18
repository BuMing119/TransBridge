"""Immutable translation-memory query and arbitration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.contracts import Diagnostic
from transbridge.application.io import EntryKey, Provenance


class TmMatchStatus(StrEnum):
    EXACT = "exact"
    STALE = "stale"
    TEXT = "text"


class TmConflictPolicy(StrEnum):
    REQUIRE_CONFIRMATION = "require_confirmation"
    PREFER_PROJECT = "prefer_project"
    EXPLICIT_DICTIONARY = "explicit_dictionary"


@dataclass(frozen=True, slots=True)
class TranslationMemoryQuery:
    entry_key: EntryKey
    original: str
    source_locale: str
    target_locale: str
    stage: int
    source_fingerprint: str
    conflict_policy: TmConflictPolicy = TmConflictPolicy.REQUIRE_CONFIRMATION
    selected_dictionary_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_locale.strip() or not self.target_locale.strip():
            raise ValueError("translation-memory query locales must be explicit")
        if not self.source_fingerprint.strip():
            raise ValueError("translation-memory query source fingerprint must be explicit")
        if self.conflict_policy is TmConflictPolicy.EXPLICIT_DICTIONARY:
            if not self.selected_dictionary_id or not self.selected_dictionary_id.strip():
                raise ValueError("explicit dictionary arbitration requires a dictionary id")
        elif self.selected_dictionary_id is not None:
            raise ValueError("selected dictionary id requires explicit arbitration")


@dataclass(frozen=True, slots=True)
class TranslationMemoryCandidate:
    entry_key: EntryKey
    translation: str
    dictionary_id: str
    dictionary_revision: int
    dictionary_scope: str
    match_status: TmMatchStatus
    matched_via: str
    reasons: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        if not self.translation or not self.dictionary_id:
            raise ValueError("translation-memory candidate values must not be empty")
        if self.matched_via not in {"key", "text"}:
            raise ValueError("translation-memory candidate match must be key or text")


@dataclass(frozen=True, slots=True)
class TranslationMemoryQueryResult:
    candidates: tuple[TranslationMemoryCandidate, ...] = ()
    selected: TranslationMemoryCandidate | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    requires_confirmation: bool = False
    blocks_publish: bool = False
    cancelled: bool = False

    @property
    def unresolved(self) -> bool:
        return self.selected is None and not self.blocks_publish and not self.cancelled
