"""Bounded, revision-aware compiled terminology views for repeated lookups."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager
from functools import wraps
from threading import Condition, RLock, get_ident
from types import MappingProxyType
from typing import TYPE_CHECKING

from transbridge.application.terminology.identity import normalize_original

from .exact_term_lookup import ExactTermLookup
from .required_term_matching import MandatoryTermMatcher
from .shared_term_snapshot import FrozenTermEntry
from .term_validation import valid_term_pair, valid_term_text

if TYPE_CHECKING:
    from transbridge.application.terminology.effective import TerminologyLookupContext

    from .shared_term_pool import SharedTermSnapshot
    from .term_database import TermDatabaseManager


class TermLookupView:
    """Own immutable scalar mappings; lazily compile only mandatory matching."""

    def __init__(self, forms: Mapping[str, tuple[str, str, bool]], *, exact: ExactTermLookup | None = None) -> None:
        self.forms = MappingProxyType(dict(forms))
        self.exact = exact or ExactTermLookup(())
        self._matcher = None
        self._lock = RLock()

    def match(self, text: str) -> dict[str, str]:
        with self._lock:
            if self._matcher is None:
                self._matcher = MandatoryTermMatcher(self.forms)
            matcher = self._matcher
        return matcher.match(text)


class _ObjectIdentity:
    """Hash by identity while retaining the object, including rolled-back views."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return id(self.value)

    def __eq__(self, other):
        return isinstance(other, _ObjectIdentity) and self.value is other.value


class TermLookupCache:
    """Serialize view creation, retain at most a small number of full scopes.

    Revisions are read before and after constructing a view. A concurrent write
    can invalidate its reuse, but can never label an old view with a new revision.
    Reads take known database locks before the cache lock. Reload markers never
    retain these locks during source I/O; callbacks fail closed if waiting for a
    concurrent reload would retain a publication lock.
    """

    def __init__(self, capacity: int = 16) -> None:
        if capacity < 1:
            raise ValueError("term lookup cache capacity must be positive")
        self._capacity = capacity
        self._lock = RLock()
        self._ready = Condition(self._lock)
        self._reload_owner = None
        self._reload_depth = 0
        self._generation = 0
        self._views = OrderedDict()
        self._shared = OrderedDict()
        self._legacy = None

    @contextmanager
    def reloading(self, manager):
        with self._ready:
            self._wait_for_reload(manager)
            self._reload_owner = get_ident()
            self._reload_depth += 1
            self._invalidate()
        try:
            # Remote source/embedding I/O must not own the project pool lock.
            yield
        finally:
            with self._ready:
                self._invalidate()
                self._reload_depth -= 1
                if self._reload_depth == 0:
                    self._reload_owner = None
                    self._ready.notify_all()

    def _wait_for_reload(self, manager):
        with self._ready:
            while self._reload_owner not in (None, get_ident()):
                # A publication callback already owns the pool. Waiting here
                # would prevent the reloader from reading that same pool.
                if self._owns_database_lock(manager):
                    raise RuntimeError("术语来源正在重新加载，不能在术语发布事务内等待，请重试")
                self._ready.wait()

    @contextmanager
    def _reading(self, manager):
        while True:
            self._wait_for_reload(manager)
            with self._reading_databases(manager), self._lock:
                # A reload may have started between waiting and taking the
                # database lock. Release it before waiting for source I/O.
                if self._reload_owner not in (None, get_ident()):
                    continue
                yield
                return

    @staticmethod
    def _owns_database_lock(manager):
        from .shared_term_pool import SharedTermPool
        from .term_database import DynamicTermDatabase

        for database in (getattr(manager, "_dynamic_db", None), getattr(manager, "_local_dynamic_db", None)):
            if isinstance(database, (DynamicTermDatabase, SharedTermPool)):
                owns = getattr(database._lock, "_is_owned", None)
                if callable(owns) and owns():
                    return True
        return False

    def _invalidate(self):
        self._generation += 1
        self._views.clear()
        self._shared.clear()
        self._legacy = None

    def view(self, manager: TermDatabaseManager, context: TerminologyLookupContext | None) -> TermLookupView:
        if not getattr(manager, "_load_completed", False) and not manager._merged_terms:
            manager.load_all()
        with self._reading(manager):
            before = self._stamp(manager, context)
            if before is not None and before in self._views:
                self._views.move_to_end(before)
                return self._views[before]
            view = build_lookup_view(manager, context)
            if before is not None and before == self._stamp(manager, context):
                self._remember(self._views, before, view)
            return view

    @staticmethod
    @contextmanager
    def _reading_databases(manager):
        # Database -> cache is shared by reads and publication callbacks.
        # Holding the database locks through construction prevents an in-place
        # local update from mixing different revisions in one returned view.
        from .shared_term_pool import SharedTermPool
        from .term_database import DynamicTermDatabase

        with ExitStack() as stack:
            if not getattr(manager, "_retrieval_enabled", True):
                yield
                return
            for database in (manager.get_dynamic_db(), getattr(manager, "_local_dynamic_db", None)):
                if isinstance(database, (DynamicTermDatabase, SharedTermPool)):
                    stack.enter_context(database._lock)
            yield

    def shared_view(
        self,
        manager: TermDatabaseManager,
        context: TerminologyLookupContext | None,
        snapshot: SharedTermSnapshot | None,
    ) -> tuple[SharedTermSnapshot, TermLookupView]:
        """Retain a supplied frozen batch snapshot, never replace it with latest.

        The snapshot's transaction/scope identity remains stable if a pool's
        bounded scope cache evicts and reconstructs it. A rolled-back revision
        receives a different transaction token when the next publish succeeds.
        """
        pool = manager._shared_term_pool
        plugin = None if context is None else context.plugin_id
        with self._reading(manager):
            captured = snapshot if snapshot is not None else pool.snapshot(plugin)
            key = captured.cache_identity, context
            if key in self._shared:
                self._shared.move_to_end(key)
                return self._shared[key]
            view = TermLookupView({
                item.term: (item.term, item.translation, item.case_sensitive) for item in captured.records
            })
            result = captured, view
            self._remember(self._shared, key, result)
            return result

    def shared_legacy_candidates(self, manager) -> Mapping[str, FrozenTermEntry]:
        """Index loaded source precedence once; unknown mutable providers stay live."""
        with self._reading(manager):
            before = self._stamp(manager, None)
            if before is not None and self._legacy is not None and self._legacy[0] == before:
                return self._legacy[1]
            candidates = MappingProxyType({
                normalize_original(entry.term): FrozenTermEntry.capture(entry)
                for entry in getattr(manager, "_legacy_terms", None) or ()
            })
            if before is not None and before == self._stamp(manager, None):
                self._legacy = before, candidates
            return candidates

    def _remember(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._capacity:
            cache.popitem(last=False)

    def _stamp(self, manager, context):
        if not getattr(manager, "_load_completed", False):
            return None
        database = manager.get_dynamic_db()
        revision = getattr(database, "revision", None)
        # Third-party/test providers with mutable records have no invalidation
        # contract. Keep their existing live lookup semantics instead of guessing.
        if not isinstance(revision, int):
            return None
        local = getattr(manager, "_local_dynamic_db", None)
        local_revision = getattr(local, "revision", None)
        if local is not None and not isinstance(local_revision, int):
            return None
        lookup = context or getattr(manager, "_terminology_context", None)
        loader = getattr(manager, "_effective_loader", None)
        identity = "legacy-global"
        if loader is not None and lookup is not None:
            identity = loader.snapshot_identity(lookup)
        return (
            lookup,
            _ObjectIdentity(loader),
            identity,
            self._generation,
            _ObjectIdentity(database),
            revision,
            _ObjectIdentity(getattr(database, "_entries", None)),
            _ObjectIdentity(local),
            local_revision,
            _ObjectIdentity(getattr(manager, "_legacy_terms", None)),
            _ObjectIdentity(manager._merged_terms),
        )


def build_lookup_view(manager, context) -> TermLookupView:
    """Preserve primary/alias insertion priority from the historical manager."""
    entries = manager._effective_terms() if context is None else manager._effective_terms(context)
    forms = {}
    for entry in entries:
        if not valid_term_pair(entry.term, entry.translation):
            continue
        forms[entry.term] = entry.term, entry.translation, entry.case_sensitive
        for variant in entry.variants:
            if valid_term_text(variant) and variant not in forms:
                forms[variant] = entry.term, entry.translation, entry.case_sensitive
    return TermLookupView(forms, exact=ExactTermLookup(entries))


def lookup_view(manager, context=None) -> TermLookupView:
    if not getattr(manager, "_retrieval_enabled", True):
        return TermLookupView({})
    cache = getattr(manager, "_lookup_cache", None)
    return build_lookup_view(manager, context) if cache is None else cache.view(manager, context)


def invalidate_lookup_load(method: Callable) -> Callable:
    """Keep source loading atomic relative to this manager's cached views."""

    @wraps(method)
    def load(manager, *args, **kwargs):
        cache = getattr(manager, "_lookup_cache", None)
        if cache is None:
            return method(manager, *args, **kwargs)
        with cache.reloading(manager):
            return method(manager, *args, **kwargs)

    return load


def revisioned_dynamic_mutation(method: Callable) -> Callable:
    """Version supported local-database writes, including exceptional reloads."""

    @wraps(method)
    def mutate(database, *args, **kwargs):
        with database._lock:
            try:
                return method(database, *args, **kwargs)
            finally:
                database._revision += 1

    return mutate
