"""Explicit saved-row to selected-input correspondence shown before supplementation."""

from dataclasses import dataclass

from transbridge.application.io.identity import EntryKey


@dataclass(frozen=True, slots=True)
class SourceSupplementChoice:
    key: EntryKey
    label: str
    original: str


@dataclass(frozen=True, slots=True)
class SourceSupplementRow:
    old_key: EntryKey
    translation: str
    matched_key: EntryKey | None


@dataclass(frozen=True, slots=True)
class SourceSupplementPreview:
    token: str
    source_name: str
    replacement_path: str
    variant_count: int
    matched_count: int
    unmatched_count: int
    added_count: int
    rows: tuple[SourceSupplementRow, ...]
    choices: tuple[SourceSupplementChoice, ...]
