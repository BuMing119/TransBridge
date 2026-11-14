# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""One Variant's bounded cache; edits invalidate indexed entries without scanning it."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import logging
from threading import Lock, RLock

from .alignment_cache_codec import decode_decision, encode_decision, location_key, same_location_input
from .occurrence_alignment import FrozenAlignmentResolver, checked_decision

logger = logging.getLogger(__name__)


class VariantAlignmentCache:
    def __init__(self, backend, variant_id, *, max_entries=10000, max_per_entry=16):
        if min(max_entries, max_per_entry) < 1:
            raise ValueError("定位缓存容量必须为正数")
        self.backend, self.variant_id = backend, variant_id
        self._max_entries, self._max_per_entry = max_entries, max_per_entry
        self._records, self._versions, self._seen_stamps, self._tokens = {}, {}, {}, {}
        self._lock, self._work_lock = RLock(), Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alignment-cache")
        self._pending = None
        self._failure = None
        self._tracker = self._unsubscribe = None
        self._closed = False
        self._epoch = 0

    def clear_all(self):
        with self._lock:
            self._epoch += 1
            self._records.clear()
            self._tokens.clear()
            self._enqueue_clear()

    def _enqueue_clear(self, keys=None):
        self._pending = self._executor.submit(self.backend.clear, self.variant_id, keys)
        self._pending.add_done_callback(self._cleared)

    def _cleared(self, future):
        error = future.exception()
        if error is not None:
            logger.error("Cannot invalidate translation position cache: %s", error, exc_info=error)
            with self._lock:
                self._failure = error

    def bind_tracker(self, tracker):
        with self._lock:
            if tracker is self._tracker:
                return
            if self._unsubscribe is not None:
                self._unsubscribe()
            self._tracker = tracker
            self._unsubscribe = tracker.subscribe(self._edited)
        self._edited(tracker.changed_keys())

    def _edited(self, keys):
        changed = []
        with self._lock:
            for key in keys:
                stamp = self._tracker.stamp(key)
                serialized = key.serialize()
                if self._seen_stamps.get(serialized) is stamp:
                    continue
                self._seen_stamps[serialized] = stamp
                changed.append(serialized)
            self.invalidate(changed)

    def invalidate(self, entry_keys):
        """Called once per committed edit, including a revert; no I/O on this thread."""
        keys = tuple(dict.fromkeys(entry_keys))
        if not keys:
            return
        with self._lock:
            if self._closed:
                return
            for key in keys:
                self._versions[key] = self._versions.get(key, 0) + 1
                self._records.pop(key, None)
                self._tokens.pop(key, None)
            self._enqueue_clear(keys)

    def flush(self):
        with self._lock:
            pending = self._pending
        if pending is not None:
            try:
                pending.result()
            except Exception as exc:
                raise RuntimeError("无法清理已修改词条的定位缓存，请检查项目存储权限。") from exc
        with self._lock:
            if self._failure is not None:
                raise RuntimeError(
                    "定位缓存清理失败，不能复用旧判断；请检查项目存储后重新打开工程。"
                ) from self._failure

    @contextmanager
    def _work(self, cancellation):
        # One in-flight lookup/model batch per Variant shares newly published results.
        while not self._work_lock.acquire(timeout=0.05):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
        try:
            if self._closed:
                raise RuntimeError("当前版本的定位缓存已经关闭，请重新打开工程。")
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            yield
        finally:
            self._work_lock.release()

    def decide(self, requests, service=None, *, retry_uncertain=False, cancellation=None, progress=None):
        requests = tuple(requests)
        if not requests:
            return ()
        with self._work(cancellation):
            return self._decide(requests, service, retry_uncertain, cancellation, progress)

    def _decide(self, requests, service, retry_uncertain, cancellation, progress):
        self.flush()
        with self._lock:
            versions = {r.entry_key: self._versions.get(r.entry_key, 0) for r in requests}
            epoch = self._epoch
        # Only ambiguous entries reach this batch. Indexed fences also detect
        # saved changes from other processes, including edit-and-revert cycles.
        snapshot = self.backend.capture(self.variant_id, ((r.entry_key, r.original, r.translation) for r in requests))
        with self._lock:
            for request in requests:
                key = request.entry_key
                previous = self._tokens.get(key)
                current = snapshot.tokens.get(key)
                if previous is not None and previous != current:
                    self._records.pop(key, None)
                self._tokens[key] = current
            decisions = [self._lookup(r, retry_uncertain) for r in requests]
        keys, trusted = [location_key(request) for request in requests], set()
        for i, request in enumerate(requests):
            for stored_key, payload in snapshot.records.get(request.entry_key, ()):
                if stored_key != keys[i]:
                    continue
                try:
                    decision = decode_decision(request, payload)
                    if decision.certain:
                        trusted.add(i)
                        decisions[i] = decision
                except (ValueError, TypeError, KeyError) as exc:
                    logger.warning("Ignoring invalid position cache for %s: %s", request.entry_key, exc)
                break
        missing = [i for i, decision in enumerate(decisions) if decision is None]
        if missing and service is not None:
            values = service.resolve_many(
                tuple(requests[i] for i in missing), cancellation=cancellation, progress=progress
            )
            if len(values) != len(missing):
                raise ValueError("AI 位置判断结果数量与请求不一致，未应用本批结果")
            for i, value in zip(missing, values):
                decisions[i] = checked_decision(value, requests[i])
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        persisted = []
        with self._lock:
            for i, (request, decision) in enumerate(zip(requests, decisions)):
                if (
                    self._closed
                    or epoch != self._epoch
                    or versions[request.entry_key] != self._versions.get(request.entry_key, 0)
                ):
                    decisions[i] = None
                    continue
                if decision is not None:
                    if decision.certain and request.entry_key in snapshot.tokens and i not in trusted:
                        persisted.append((request.entry_key, keys[i], encode_decision(request, decision)))
        if persisted:
            self.backend.publish(snapshot, persisted)
        valid = self.backend.valid_entries(snapshot)
        with self._lock:
            for i, (request, decision) in enumerate(zip(requests, decisions)):
                if (
                    self._closed
                    or epoch != self._epoch
                    or versions[request.entry_key] != self._versions.get(request.entry_key, 0)
                    or (request.entry_key in snapshot.tokens and request.entry_key not in valid)
                ):
                    decisions[i] = None
                    self._records.pop(request.entry_key, None)
                elif decision is not None:
                    self._remember(request, decision)
        return tuple(decisions)

    def _lookup(self, request, retry_uncertain):
        for previous, decision in reversed(self._records.get(request.entry_key, ())):
            if same_location_input(previous, request) and (decision.certain or not retry_uncertain):
                return decision
        return None

    def _remember(self, request, decision):
        group = self._records.setdefault(request.entry_key, [])
        group[:] = [
            (old, value)
            for old, value in group
            if old.original == request.original
            and old.translation == request.translation
            and not same_location_input(old, request)
        ]
        group.append((request, decision))
        del group[: -self._max_per_entry]
        while len(self._records) > self._max_entries:
            self._records.pop(next(iter(self._records)))

    def freeze(self):
        """Worker-only immutable export; never called from the edit notification."""
        with self._lock:
            records = tuple(record for group in self._records.values() for record in group)
        return FrozenAlignmentResolver(records)

    def close(self):
        with self._lock:
            self._closed = True
            self._epoch += 1
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
        self._executor.shutdown(wait=True)
        self.flush()
