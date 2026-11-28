# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Ranked semantic references for individual translation entries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from transbridge.application.terminology.identity import normalize_original

from .term_validation import valid_term_pair

if TYPE_CHECKING:
    from .term_vector_index import TermVectorIndex

RELATED_TERM_LIMIT = 3


def hybrid_terms_by_original(
    index: TermVectorIndex, originals: list[str], substring_matches: Mapping[str, str]
) -> dict[str, dict[str, str]]:
    """Keep hybrid retrieval scoped to queries without a literal term hit."""
    matched = [term.lower() for term in substring_matches]
    queries = []
    for original in originals:
        text = original.lower()
        if not any(term in text for term in matched):
            queries.append(original)
    results = index.search_hybrid_batch(queries, top_k=RELATED_TERM_LIMIT)
    return {
        original: {row.term: row.translation for row in rows if valid_term_pair(row.term, row.translation)}
        for original, rows in results.items()
    }


def select_related_terms(terms: Mapping[str, str], candidates: Mapping[str, str]) -> dict[str, str]:
    """Prefer final direct matches and retain the retrieval order of remaining candidates."""
    if not candidates:
        return {}
    seen = {normalize_original(term) for term in terms}
    selected = {}
    for term, translation in candidates.items():
        key = normalize_original(term)
        if key in seen:
            continue
        selected[term] = translation
        seen.add(key)
        if len(selected) == RELATED_TERM_LIMIT:
            break
    return selected
