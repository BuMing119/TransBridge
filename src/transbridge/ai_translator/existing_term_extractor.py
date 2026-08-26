"""Initialize dynamic terminology from translations already present in a collection."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING
import unicodedata

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.converter.context_categories import AUTO_TERM_CONTEXTS
from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_QUESTIONABLE

if TYPE_CHECKING:
    from transbridge.ai_translator.noun_extractor import NounExtractor
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.converter.translation_entry import TranslationEntry


EXISTING_NAME_SOURCE = "existing_name"
EXISTING_TEXT_SOURCE = "existing_text"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ExistingTermSeedResult:
    """Summary of one partial-project terminology initialization."""

    direct_added: int = 0
    text_added: int = 0
    conflicts: int = 0
    skipped_existing: int = 0
    text_extraction_attempted: bool = False

    @property
    def added_count(self) -> int:
        return self.direct_added + self.text_added


def should_seed_existing_terms(
    entries: list[TranslationEntry],
    candidates: list[TranslationEntry],
) -> bool:
    """Return whether the current run is translating the unfinished part of an existing corpus."""

    has_existing_translation = any(_is_eligible_existing_entry(entry) for entry in entries)
    has_untranslated_candidate = any(not entry.translation.strip() for entry in candidates)
    return has_existing_translation and has_untranslated_candidate


class ExistingTermSeeder:
    """Extract, merge, and persist safe terminology from existing translations."""

    def __init__(
        self,
        term_manager: TermDatabaseManager,
        noun_extractor: NounExtractor,
        *,
        text_batch_size: int = 20,
    ) -> None:
        if text_batch_size <= 0:
            raise ValueError("text_batch_size must be positive")
        self._term_manager = term_manager
        self._noun_extractor = noun_extractor
        self._text_batch_size = text_batch_size

    def seed(self, entries: list[TranslationEntry]) -> ExistingTermSeedResult:
        eligible = [entry for entry in entries if _is_eligible_existing_entry(entry)]
        if not eligible:
            return ExistingTermSeedResult()

        dynamic_db = self._term_manager.get_dynamic_db()
        direct_terms = self._extract_direct_terms(eligible)

        text_extraction_attempted = not any(entry.source == EXISTING_TEXT_SOURCE for entry in dynamic_db.as_list())
        text_terms = self._extract_text_terms(eligible) if text_extraction_attempted else []

        merged, conflicts = _merge_without_conflicts([*direct_terms, *text_terms])
        accepted: list[TermEntry] = []
        skipped_existing = 0
        for term in merged:
            if self._term_manager.has_term(term.term):
                skipped_existing += 1
                continue
            accepted.append(term)

        if accepted:
            dynamic_db.add_many_and_save([
                (entry.term, entry.translation, entry.source, entry.context) for entry in accepted
            ])

        return ExistingTermSeedResult(
            direct_added=sum(entry.source == EXISTING_NAME_SOURCE for entry in accepted),
            text_added=sum(entry.source == EXISTING_TEXT_SOURCE for entry in accepted),
            conflicts=conflicts,
            skipped_existing=skipped_existing,
            text_extraction_attempted=text_extraction_attempted,
        )

    @staticmethod
    def _extract_direct_terms(entries: list[TranslationEntry]) -> list[TermEntry]:
        terms: list[TermEntry] = []
        for entry in entries:
            context = entry.context or ""
            context_base = context.split("|", 1)[0]
            if context_base not in AUTO_TERM_CONTEXTS:
                continue
            terms.append(
                TermEntry(
                    term=entry.original.strip(),
                    translation=entry.translation.strip(),
                    source=EXISTING_NAME_SOURCE,
                    context=context,
                )
            )
        return terms

    def _extract_text_terms(self, entries: list[TranslationEntry]) -> list[TermEntry]:
        pairs = [
            {"original": entry.original, "translation": entry.translation}
            for entry in entries
            if (entry.context or "").split("|", 1)[0] not in AUTO_TERM_CONTEXTS
        ]
        terms: list[TermEntry] = []
        for start in range(0, len(pairs), self._text_batch_size):
            extracted = self._noun_extractor.extract(pairs[start : start + self._text_batch_size])
            terms.extend(
                TermEntry(
                    term=entry.term,
                    translation=entry.translation,
                    source=EXISTING_TEXT_SOURCE,
                )
                for entry in extracted
            )
        return terms


def _is_eligible_existing_entry(entry: TranslationEntry) -> bool:
    return bool(
        entry.original.strip() and entry.translation.strip() and entry.stage not in {STAGE_HIDDEN, STAGE_QUESTIONABLE}
    )


def _normalized_source(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _normalized_translation(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _merge_without_conflicts(entries: list[TermEntry]) -> tuple[list[TermEntry], int]:
    grouped: dict[str, dict[str, TermEntry]] = {}
    for entry in entries:
        term = entry.term.strip()
        translation = entry.translation.strip()
        if not term or not translation:
            continue
        source_key = _normalized_source(term)
        target_key = _normalized_translation(translation)
        grouped.setdefault(source_key, {}).setdefault(target_key, entry)

    merged: list[TermEntry] = []
    conflicts = 0
    for translations in grouped.values():
        if len(translations) != 1:
            conflicts += 1
            continue
        merged.append(next(iter(translations.values())))
    return merged, conflicts


__all__ = [
    "EXISTING_NAME_SOURCE",
    "EXISTING_TEXT_SOURCE",
    "ExistingTermSeedResult",
    "ExistingTermSeeder",
    "should_seed_existing_terms",
]
