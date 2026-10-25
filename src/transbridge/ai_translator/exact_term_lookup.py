"""Exact terminology lookup preserving canonical terms and alias precedence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from transbridge.application.terminology.identity import normalize_translation

from .term_validation import valid_term_pair, valid_term_text

if TYPE_CHECKING:
    from .term_formats import TermEntry


class ExactTermLookup:
    """Build once per effective scope; preserve the manager's precedence."""

    def __init__(self, entries: Iterable[TermEntry]) -> None:
        self._sensitive: dict[str, tuple[str, str]] = {}
        self._folded: dict[str, tuple[str, str]] = {}
        for entry in entries:
            if not valid_term_pair(entry.term, entry.translation):
                continue
            for spelling in (entry.term, *entry.variants):
                if not valid_term_text(spelling):
                    continue
                normalized = normalize_translation(spelling)
                if entry.case_sensitive:
                    self._sensitive[normalized] = entry.term, entry.translation
                else:
                    self._folded[normalized.casefold()] = entry.term, entry.translation

    def translation_for(self, original: str) -> str | None:
        match = self.term_for(original)
        return None if match is None else match[1]

    def term_for(self, original: str) -> tuple[str, str] | None:
        """Return the canonical spelling and translation, preserving alias priority."""
        normalized = normalize_translation(original)
        return self._sensitive.get(normalized, self._folded.get(normalized.casefold()))

    def match(self, originals: Iterable[str]) -> dict[str, str]:
        return {match[0]: match[1] for original in originals if (match := self.term_for(original)) is not None}
