"""Match mandatory terminology without substring or reverse-recall noise."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import TYPE_CHECKING

from .term_validation import valid_term_pair, valid_term_text

if TYPE_CHECKING:
    from .term_formats import TermEntry


class MandatoryTermMatcher:
    """Validate and compile one immutable terminology view for repeated matches."""

    def __init__(self, matcher_map: Mapping[str, tuple[str, str, bool]]) -> None:
        patterns = []
        for order, (form, (term, translation, case_sensitive)) in enumerate(matcher_map.items()):
            if not valid_term_text(form) or not valid_term_pair(term, translation):
                continue
            left = r"(?<!\w)" if form[0].isascii() and (form[0].isalnum() or form[0] == "_") else ""
            right = r"(?!\w)" if form[-1].isascii() and (form[-1].isalnum() or form[-1] == "_") else ""
            pattern = re.compile(left + re.escape(form) + right, 0 if case_sensitive else re.IGNORECASE)
            patterns.append((pattern, order, term, translation))
        self._patterns = tuple(patterns)

    @classmethod
    def from_terms(cls, entries: Iterable[TermEntry]) -> MandatoryTermMatcher:
        """Retain the effective view's primary-term and alias precedence."""
        forms = {}
        for entry in entries:
            if not valid_term_pair(entry.term, entry.translation):
                continue
            forms[entry.term] = (entry.term, entry.translation, entry.case_sensitive)
            for variant in entry.variants:
                if valid_term_text(variant) and variant not in forms:
                    forms[variant] = (entry.term, entry.translation, entry.case_sensitive)
        return cls(forms)

    def match(self, text: str) -> dict[str, str]:
        """Prefer longest non-overlapping spans; keep aliases and source order."""
        matches = [
            (match.start(), match.end(), order, term, translation)
            for pattern, order, term, translation in self._patterns
            for match in pattern.finditer(text)
        ]
        occupied: list[tuple[int, int]] = []
        selected = []
        for start, end, order, term, translation in sorted(
            matches, key=lambda item: (item[0] - item[1], item[0], item[2])
        ):
            if any(start < other_end and other_start < end for other_start, other_end in occupied):
                continue
            occupied.append((start, end))
            selected.append((start, order, term, translation))
        return {term: translation for _, _, term, translation in sorted(selected)}


def match_required_terms(text: str, matcher_map: Mapping[str, tuple[str, str, bool]]) -> dict[str, str]:
    """Match one request, preserving the original public mapping-based API.

    Latin word fragments and reverse prefix/suffix recall are useful as search
    hints, but cannot establish mandatory terminology constraints. A shorter
    term still applies when it occurs independently elsewhere in the source.
    Repeated callers should retain a MandatoryTermMatcher for their frozen view.
    """
    return MandatoryTermMatcher(matcher_map).match(text)
