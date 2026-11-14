# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Own independent Variant caches for a terminology application lifetime."""

from threading import RLock

from transbridge.application.terminology_profiles.alignment_cache import VariantAlignmentCache
from transbridge.persistence.project_store.alignment_cache import ProjectAlignmentCache


class AlignmentCacheRegistry:
    def __init__(self):
        self._items = {}
        self._lock = RLock()

    def for_variant(self, repositories, lifecycle, project_id, variant_id):
        if not project_id or not variant_id:
            raise ValueError("Alignment caches require Project and Variant identities")
        key = project_id, variant_id
        with self._lock:
            item = self._items.get(key)
            if item is None:
                # The accessor validates a path; no database is opened here.
                item = VariantAlignmentCache(
                    ProjectAlignmentCache(repositories.alignment_cache_path(project_id)), variant_id
                )
                self._items[key] = item
            active = None if lifecycle is None else lifecycle.active
            if (
                active is not None
                and active.variant is not None
                and active.project_ref.identity.value == project_id
                and active.variant.ref.identity.value == variant_id
            ):
                item.bind_tracker(active.variant.alignment_epochs)
            return item

    def close_project(self, project_id):
        with self._lock:
            items = [self._items.pop(key) for key in tuple(self._items) if key[0] == project_id]
        for item in items:
            item.close()

    def close(self):
        with self._lock:
            items = tuple(self._items.values())
            self._items.clear()
        for item in items:
            item.close()
