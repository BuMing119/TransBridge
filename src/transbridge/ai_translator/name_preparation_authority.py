"""Freeze the effective and existing-source constraints used to group names."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transbridge.application.terminology.effective import EffectiveTerminologyIndex
from transbridge.application.translation.protected_syntax import protected_syntax_matches
from transbridge.converter.context_categories import name_category
from transbridge.converter.translation_entry import STAGE_CHECKED, STAGE_REVIEWED, STAGE_TRANSLATED

from .exact_term_lookup import ExactTermLookup
from .name_preparation_plan import NameAuthority
from .required_term_matching import MandatoryTermMatcher
from .term_validation import valid_term_pair

if TYPE_CHECKING:
    from transbridge.application.io.identity import EntryKey
    from transbridge.converter.translation_entry import TranslationEntry

    from .term_database import TermDatabaseManager
    from .term_formats import TermEntry


@dataclass(slots=True)
class _ScopeAuthority:
    exact: ExactTermLookup
    published: EffectiveTerminologyIndex | None
    entries: tuple[TermEntry, ...]
    matcher: MandatoryTermMatcher | None = None


class SourceNameAuthority:
    """Index each effective plugin scope once, without semantic search or writes."""

    def __init__(
        self,
        manager: TermDatabaseManager,
        entries: Iterable[TranslationEntry],
        *,
        excluded: set[EntryKey],
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        self.manager = manager
        self._scopes = {}
        self._known = defaultdict(set)
        self._cache = {}
        for entry in entries:
            if checkpoint is not None:
                checkpoint()
            category = name_category(entry.context)
            if (
                category is not None
                and entry.identity not in excluded
                and entry.stage in {STAGE_TRANSLATED, STAGE_CHECKED, STAGE_REVIEWED}
                and valid_term_pair(entry.original, entry.translation)
                and protected_syntax_matches(entry.original, entry.translation)
            ):
                context = manager.lookup_context_for_entry(entry)
                self._known[(context, entry.original, category)].add(entry.translation)

    def for_entry(self, entry: TranslationEntry, *, check_required_terms: bool = True) -> NameAuthority:
        context = self.manager.lookup_context_for_entry(entry)
        key = (context, entry.original, name_category(entry.context), check_required_terms)
        if key in self._cache:
            return self._cache[key]
        if context not in self._scopes:
            loader = getattr(self.manager, "_effective_loader", None)
            published = (
                EffectiveTerminologyIndex(loader.effective_snapshot(context))
                if loader is not None and context is not None and loader.enabled(context)
                else None
            )
            entries = tuple(self.manager._effective_terms(context))
            self._scopes[context] = _ScopeAuthority(ExactTermLookup(entries), published, entries)
        scope = self._scopes[context]
        translated = scope.exact.translation_for(entry.original)
        blocked = (
            scope.published is not None and scope.published.resolve(entry.original, context).blocks_legacy_fallback
        )
        required = ()
        if check_required_terms:
            if scope.matcher is None:
                scope.matcher = MandatoryTermMatcher.from_terms(scope.entries)
            required = tuple(sorted(scope.matcher.match(entry.original).items()))
        authority = NameAuthority(
            translated,
            blocked,
            required,
            tuple(sorted(self._known.get(key[:3], ()))) if translated is None and not blocked else (),
        )
        self._cache[key] = authority
        return authority
