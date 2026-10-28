"""Bounded collection invalidation, including deprecated mutable entry edits.

Subscriptions are external to entries so shallow/deep copies and snapshots stay
detached. Neither the registry nor an entry keeps an old collection alive.
"""

from collections import deque
from contextlib import ExitStack
from threading import RLock
import weakref

_registry = {}
_registry_lock = RLock()
JOURNAL_LIMIT = 4096


def _observe(entry, journal):
    identity = id(entry)
    with _registry_lock:
        existing = _registry.get(identity)
        if existing is None or existing[0]() is not entry:

            def discard(reference):
                with _registry_lock:
                    if _registry.get(identity, (None,))[0] is reference:
                        _registry.pop(identity, None)

            existing = weakref.ref(entry, discard), weakref.WeakSet()
            _registry[identity] = existing
        existing[1].add(journal)


def assign_entry_field(entry, name, value):
    """Publish an in-place compatibility edit atomically to every owning reader."""
    while True:
        with _registry_lock:
            observed = _registry.get(id(entry))
            journals = tuple(observed[1]) if observed is not None and observed[0]() is entry else ()
        with ExitStack() as locks:
            for journal in sorted(journals, key=id):
                locks.enter_context(journal.lock)
            # A collection can subscribe while we wait for its sibling's lock.
            # Retry with the complete lock set before assigning anything. Never
            # hold the registry lock while acquiring a journal: publication
            # already takes those locks in journal -> registry order.
            with _registry_lock:
                observed = _registry.get(id(entry))
                current = tuple(observed[1]) if observed is not None and observed[0]() is entry else ()
                if set(current) != set(journals):
                    continue
                object.__setattr__(entry, name, value)
                for journal in journals:
                    if journal.entries.get(entry.identity.serialize()) is entry:
                        journal.record((entry.identity,))
                return


class CollectionChangeJournal:
    """A monotonic stamp plus a bounded delta window for independent readers."""

    def __init__(self, lock):
        self.lock = lock
        self.entries = {}
        self.version = 0
        self._events = deque(maxlen=JOURNAL_LIMIT)

    def publish(self, entries, keys=None):
        """Called under the collection lock at its existing publication boundary."""
        keys = set(self.entries) | set(entries) if keys is None else keys
        identities = set()
        for key in keys:
            before, after = self.entries.get(key), entries.get(key)
            if before is not None:
                identities.add(before.identity)
            if after is not None:
                _observe(after, self)
                identities.add(after.identity)
        self.entries = entries
        self.record(identities)

    def record(self, identities):
        for identity in identities:
            self.version += 1
            self._events.append((self.version, identity))

    def since(self, version):
        """Return changed keys, or None when a reader needs one fresh collection."""
        if version == self.version:
            return set()
        if version is None or not self._events or version < self._events[0][0] - 1:
            return None
        changed = set()
        for stamp, identity in reversed(self._events):
            if stamp <= version:
                break
            changed.add(identity)
        return changed
