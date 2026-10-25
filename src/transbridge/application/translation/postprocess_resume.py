"""Bind durable phase identity to one fresh execution without changing its commit owner."""

from dataclasses import replace

from .postprocess_checkpoint import FilesystemPostProcessCheckpointPort
from .workload_models import canonical_hash, translation_input_fingerprint


class ExecutionPostProcessCheckpoint:
    """A private adapter; stable storage identity never authorizes a collection write."""

    def __init__(self, root, *, run_id, identity, settings, entries, stages):
        self._port = FilesystemPostProcessCheckpointPort(root)
        self._run_id = run_id
        self._inputs = {entry.entry_key: entry for entry in entries}
        self._input_fingerprint = translation_input_fingerprint(entries)
        self._storage_id = (
            identity
            + "-post-"
            + canonical_hash({
                "settings": settings,
                "input": [
                    {key: value for key, value in entry.to_dict().items() if key != "revision"}
                    for entry in sorted(entries, key=lambda item: item.entry_key)
                ],
                "stages": list(stages),
            })
        )

    def load(self, run_id):
        if run_id != self._run_id:
            raise ValueError("post-process recovery belongs to another execution")
        checkpoint = self._port.load(self._storage_id)
        if checkpoint is None:
            return None
        from transbridge.application.io.identity import EntryKey

        restored = []
        for entry in checkpoint.entries:
            key = EntryKey.from_dict(entry.entry_key)
            current = self._inputs.get(key)
            if current is None or (entry.original, entry.before_text, entry.context) != (
                current.original,
                current.translation,
                current.context,
            ):
                raise ValueError("post-process recovery input content does not match the current draft")
            restored.append(replace(entry, before_revision=current.revision.value))
        return replace(checkpoint, run_id=run_id, input_fingerprint=self._input_fingerprint, entries=tuple(restored))

    def save(self, checkpoint):
        if checkpoint.run_id != self._run_id:
            raise ValueError("post-process recovery belongs to another execution")
        self._port.save(replace(checkpoint, run_id=self._storage_id))
