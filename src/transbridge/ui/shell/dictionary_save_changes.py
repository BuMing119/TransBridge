# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Keep recent committed changes across saves until a source next publishes its dictionary."""

from transbridge.application.io.identity import EntryKey
from transbridge.application.projects.save_result import MAX_SAVE_ENTRY_DELTA_KEYS

_MAX_BATCHES = 64


class DictionarySaveChanges:
    def __init__(self):
        self._scope = None
        self._batches = ()

    def record(self, receipt):
        """Return a stable window for the background writer, including earlier timed saves."""
        batch = receipt.get("entry_delta")
        if batch is None:
            self._batches = ()
            return self._batches
        scope = receipt["project_id"], receipt["variant_id"], receipt["persisted_project_revision"]
        target = receipt["persisted_variant_revision"]
        if self._scope != scope:
            self._scope, self._batches = scope, ()
        if (batch["project_id"], batch["variant_id"], batch["variant_revision"]) != (*scope[:2], target):
            self._batches = ()
            return self._batches
        previous = batch["previous_variant_revision"]
        if self._batches and self._batches[-1]["variant_revision"] != previous:
            self._batches = ()
        if previous < target:
            self._batches = (*self._batches, batch)[-_MAX_BATCHES:]
            # Forgotten ranges are recovered by reading the committed source once.
            while sum(len(item["entry_keys"]) for item in self._batches) > MAX_SAVE_ENTRY_DELTA_KEYS:
                self._batches = self._batches[1:]
        return self._batches


def source_keys_since(batches, previous, target, namespace):
    """Return None unless every intervening revision is accounted for."""
    keys = {}
    for batch in reversed(batches):
        if batch["variant_revision"] != target:
            return None
        for value in batch["entry_keys"]:
            if value["namespace"] == namespace:
                keys[EntryKey.from_dict(value)] = None
        target = batch["previous_variant_revision"]
        if target == previous:
            return tuple(keys)
        if target < previous:
            break
    return None
