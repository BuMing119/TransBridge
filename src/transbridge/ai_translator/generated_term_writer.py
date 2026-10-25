"""Persist task-generated terminology and acknowledge it as one operation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
import logging
from threading import RLock
from typing import TYPE_CHECKING

from .dynamic_term_transaction import dynamic_term_transaction
from .term_publication_conflict import TermPublicationConflict

if TYPE_CHECKING:
    from transbridge.application.terminology.effective import TerminologyLookupContext

    from .term_database import TermDatabaseManager

type GeneratedTerms = list[tuple[str, str, str, str]]
type TerminologyAccess = Callable[[], AbstractContextManager]
logger = logging.getLogger(__name__)


class GeneratedTermWriter:
    """Keep all automatic write paths inside the task's terminology boundary."""

    def __init__(self, manager: TermDatabaseManager, *, access: TerminologyAccess | None = None) -> None:
        self._manager = manager
        self._access = access
        self._lock = RLock()

    def save(
        self,
        terms: GeneratedTerms,
        *,
        observer: Callable[[object, GeneratedTerms], None] | None = None,
        context: TerminologyLookupContext | None = None,
        scope_plugin_id: str | None = None,
    ) -> int:
        """Return persisted accepted rows; shared repeats/conflicts count as zero."""
        if not terms:
            return 0
        try:
            return self._save(terms, observer=observer, context=context, scope_plugin_id=scope_plugin_id)
        except TermPublicationConflict as exc:
            pool = getattr(self._manager, "_shared_term_pool", None)
            if pool is not None:
                pool.record_publication_conflict(
                    terms,
                    plugin_id=scope_plugin_id or getattr(self._manager, "_generated_term_plugin_id", None),
                )
            logger.warning("新增术语未写入：%s；保留当前译文和任务参考，任务继续", exc)
            return 0

    def _save(
        self,
        terms: GeneratedTerms,
        *,
        observer: Callable[[object, GeneratedTerms], None] | None,
        context: TerminologyLookupContext | None,
        scope_plugin_id: str | None,
    ) -> int:
        # The task gate also covers other stages and sources. The local lock
        # keeps writes and notifications ordered for standalone translators.
        with (self._access or nullcontext)(), self._lock:
            database = self._manager.get_dynamic_db()
            pool = getattr(self._manager, "_shared_term_pool", None)
            if pool is not None:
                lookup = context or getattr(self._manager, "_terminology_context", None)
                plugin_id = scope_plugin_id or getattr(self._manager, "_generated_term_plugin_id", None)
                if context is None and lookup is not None and plugin_id is not None:
                    lookup = lookup.for_plugin(plugin_id)
                if lookup is not None and pool.identity[:2] != (lookup.local_project_id, lookup.local_variant_id):
                    raise ValueError("generated terminology context belongs to another Project/Variant")
                loader = getattr(self._manager, "_effective_loader", None)

                def authority(term):
                    if loader is None or lookup is None:
                        return None, False
                    from .term_formats import TermEntry

                    sentinel = TermEntry(term, "unpublished", "shared-pool-probe")
                    resolved = loader.resolve(term, lookup, lambda _term: sentinel)
                    return (None, False) if resolved is sentinel else (resolved, resolved is None)

                accepted = pool.publish(
                    terms,
                    authority=authority,
                    plugin_id=scope_plugin_id,
                    context_plugin_id=plugin_id if lookup is None else lookup.plugin_id,
                    acknowledge=(None if observer is None else lambda accepted: observer(self._manager, accepted)),
                )
                return len(accepted)
            with dynamic_term_transaction(database):
                if self._access is not None:
                    # Another source in this task may share the same dynamic file.
                    # Its acknowledged writes must survive this manager's save.
                    database.load()
                # Reload can overlap an external writer, which does not hold the
                # task gate. Recheck its file version before persisting our values.
                with (self._access or nullcontext)():
                    database.add_many_and_save(terms)
                    if observer is not None:
                        observer(self._manager, terms)
                requested = set(terms)
                return sum(
                    (entry.term, entry.translation, entry.source, entry.context) in requested
                    for entry in database.as_list()
                )
