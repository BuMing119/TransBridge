# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Track accepted translation changes without inspecting alignment caches or UI text."""

from collections.abc import Callable, Iterable
import logging
from threading import RLock

from transbridge.application.io.identity import EntryKey

from .deferred_variant_rows import DeferredVariantRows

logger = logging.getLogger(__name__)


class TranslationEpochTracker:
    """One live Variant's sparse translation-only generations; tokens never repeat."""

    def __init__(self):
        self._epochs = {}
        self._listeners = []
        self._lock = RLock()

    def stamp(self, key: EntryKey):
        with self._lock:
            return self._epochs.get(key)

    def changed_keys(self):
        with self._lock:
            return tuple(self._epochs)

    def subscribe(self, callback: Callable[[tuple[EntryKey, ...]], None]):
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def invalidate(self, keys: Iterable[EntryKey]):
        keys = tuple(dict.fromkeys(keys))
        if not keys:
            return
        with self._lock:
            for key in keys:
                self._epochs[key] = object()
            listeners = tuple(self._listeners)
        for callback in listeners:
            try:
                callback(keys)
            except Exception:
                # The accepted edit must not be reported as rolled back. Cache
                # owners can still observe changed stamps on their next read.
                logger.exception("Unable to notify alignment cache of an accepted translation change")

    def fork(self):
        child = TranslationEpochTracker()
        with self._lock:
            child._epochs = self._epochs.copy()
        return child


def changed_translation_keys(previous, current, *, sparse=None):
    """Compare only supplied changed rows for normal deferred edit commands."""
    if current is previous:
        return ()
    if sparse is not None:
        rows = tuple(sparse)
        keys = tuple(row.entry_key for row in rows)
        key_set = set(keys)
        before = (
            previous.get_many(keys)
            if isinstance(previous, DeferredVariantRows)
            else (row for row in previous if row.entry_key in key_set)
        )
        old = {row.entry_key: row for row in before}
        return tuple(row.entry_key for row in rows if _text(old.get(row.entry_key)) != _text(row))
    if isinstance(current, DeferredVariantRows) and isinstance(previous, DeferredVariantRows):
        saved = current.committed_from(previous)
        if saved is not None:
            rows = {row.entry_key: row for row in (*saved, *current.changed_from(previous))}
            return changed_translation_keys(previous, current, sparse=rows.values())
    old = {row.entry_key: _text(row) for row in previous}
    new = {row.entry_key: _text(row) for row in current}
    return tuple(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))


def publish_alignment_epochs(previous, current):
    """Notify existing subscribers only after a replacement has been accepted."""
    if previous.ref != current.ref:
        return
    changes = changed_translation_keys(previous.snapshot().entries, current.snapshot().entries)
    # One published Variant lifetime keeps its subscriptions even when Qt
    # coalesces several authority replacements before the next view refresh.
    current.alignment_epochs = previous.alignment_epochs
    current.alignment_epochs.invalidate(changes)


def _text(row):
    return None if row is None else (row.translation, row.tombstone)
