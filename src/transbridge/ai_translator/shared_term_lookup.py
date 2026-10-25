"""Lightweight request-time lookup of committed shared terminology."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transbridge.application.terminology.identity import normalize_original

from .required_term_matching import match_required_terms

if TYPE_CHECKING:
    from .shared_term_pool import SharedTermSnapshot
    from .term_database import TermDatabaseManager


def shared_terms_for_entry(
    manager: TermDatabaseManager,
    entry: object,
    *,
    snapshot: SharedTermSnapshot | None = None,
) -> dict[str, str]:
    """Match shared records, preserving loaded legacy and published authority.

    Pass one captured snapshot per plugin when preparing a batch so every
    entry in that scope uses the same committed revision. No embedding work
    or complete legacy-source matching is repeated at admission.
    """
    pool = getattr(manager, "_shared_term_pool", None)
    if pool is None or not getattr(manager, "_retrieval_enabled", True):
        return {}
    context = manager.lookup_context_for_entry(entry)
    cache = getattr(manager, "_lookup_cache", None)
    if cache is None:
        snapshot = snapshot or pool.snapshot(None if context is None else context.plugin_id)
        matched = match_required_terms(
            getattr(entry, "original", ""),
            {item.term: (item.term, item.translation, item.case_sensitive) for item in snapshot.records},
        )
    else:
        snapshot, view = cache.shared_view(manager, context, snapshot)
        matched = view.match(getattr(entry, "original", ""))
    candidates = {term: snapshot.records_by_term[term] for term in matched}
    # A plugin's historical local terminology remains local and retains its
    # configured precedence, even if another plugin generated the same word.
    legacy_candidates = (
        {normalize_original(item.term): item for item in getattr(manager, "_legacy_terms", None) or ()}
        if cache is None
        else cache.shared_legacy_candidates(manager)
    )
    for term in matched:
        if (legacy := legacy_candidates.get(normalize_original(term))) is not None:
            candidates[term] = legacy
            matched[term] = legacy.translation
    loader = getattr(manager, "_effective_loader", None)
    if loader is not None and context is not None:
        for term in tuple(matched):
            resolved = loader.resolve(term, context, lambda value: candidates[value])
            if resolved is None:
                matched.pop(term)
            else:
                matched[term] = resolved.translation
    return matched
