# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bounded reuse of completed reading positions for immutable source structures."""

from collections import OrderedDict
from threading import Lock

_LIMIT = 2
_MAX_ITEMS = 250_000
_lock = Lock()
_cache = OrderedDict()
_items = 0
_IMMUTABLE_SCALARS = frozenset((str, int, float, bool, bytes, type(None)))


def find_reading_view(entries, evidence):
    """Exact-object reuse; retained inputs prevent identity reuse after disposal."""
    key = id(entries), id(evidence)
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] is entries and cached[1] is evidence:
            _cache.move_to_end(key)
            return cached[2]
    return None


def remember_reading_view(entries, evidence, view):
    """Retain source identity/context/metadata only, never mutable translation rows.

    The caller supplies frozen ReadingEntry values. Metadata is allowed to contain
    legacy mutable objects, so those inputs explicitly bypass this cache.
    """
    global _items

    def immutable(value):
        return type(value) in _IMMUTABLE_SCALARS or (type(value) is tuple and all(immutable(item) for item in value))

    weight = len(entries) + len(evidence.records) + len(evidence.links)
    if (
        weight > _MAX_ITEMS
        or any(type(value) is not tuple for value in (evidence.records, evidence.links, evidence.diagnostics))
        or not immutable((evidence.invalid_quests, evidence.invalid_topics, evidence.invalid_branch_quests))
        or not all(immutable(item.details) for item in evidence.diagnostics)
        or not all(immutable(entry.metadata) for entry in entries)
    ):
        return
    key = id(entries), id(evidence)
    with _lock:
        previous = _cache.pop(key, None)
        if previous is not None:
            _items -= previous[3]
        _cache[key] = entries, evidence, view, weight
        _items += weight
        while len(_cache) > _LIMIT or _items > _MAX_ITEMS:
            _, removed = _cache.popitem(last=False)
            _items -= removed[3]
