"""Match mandatory terminology without substring or reverse-recall noise."""

from collections.abc import Mapping
import re

from .term_validation import valid_term_pair, valid_term_text


def match_required_terms(text: str, matcher_map: Mapping[str, tuple[str, str, bool]]) -> dict[str, str]:
    """Prefer the longest non-overlapping source spans, including explicit aliases.

    Latin word fragments and reverse prefix/suffix recall are useful as search
    hints, but cannot establish mandatory terminology constraints. A shorter
    term still applies when it occurs independently elsewhere in the source.
    """
    matches = []
    for order, (form, (term, translation, case_sensitive)) in enumerate(matcher_map.items()):
        if not valid_term_text(form) or not valid_term_pair(term, translation):
            continue
        left = r"(?<!\w)" if form[0].isascii() and (form[0].isalnum() or form[0] == "_") else ""
        right = r"(?!\w)" if form[-1].isascii() and (form[-1].isalnum() or form[-1] == "_") else ""
        pattern = left + re.escape(form) + right
        for match in re.finditer(pattern, text, 0 if case_sensitive else re.IGNORECASE):
            matches.append((match.start(), match.end(), order, term, translation))
    occupied: list[tuple[int, int]] = []
    selected = []
    for start, end, order, term, translation in sorted(matches, key=lambda item: (item[0] - item[1], item[0], item[2])):
        if any(start < other_end and other_start < end for other_start, other_end in occupied):
            continue
        occupied.append((start, end))
        selected.append((start, order, term, translation))
    return {term: translation for _, _, term, translation in sorted(selected)}
