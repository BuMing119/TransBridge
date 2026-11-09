# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Sparse editable state with explicit capture and acknowledgement."""

from __future__ import annotations

from dataclasses import replace
from threading import Lock, RLock
from typing import Any
from uuid import uuid4

from transbridge.application.io.identity import EntryKey
from transbridge.persistence.v2.ids import VariantId
from transbridge.persistence.v2.variant import VariantEntryState

from .storage_contracts import EntryStateChange, ProjectStorePort, SaveBatch, SaveReceipt


class ProjectWorkingCopy:
    def __init__(self, store: ProjectStorePort, variant_id: VariantId) -> None:
        summary = store.summary()
        self.project_id = summary.identity
        self.variant_id = variant_id
        variant = next((item for item in summary.variants if item.identity == variant_id), None)
        if variant is None:
            raise ValueError(f"unknown Variant {variant_id.value!r}")
        self._store = store
        self._project_revision = summary.revision
        self._variant_revision = variant.revision
        self._base: dict[EntryKey, VariantEntryState] = {}
        self._pending: dict[EntryKey, dict[str, Any]] = {}
        self._captured: SaveBatch | None = None
        self._captured_fields: dict[EntryKey, frozenset[str]] = {}
        self._lock = RLock()
        self._save_lock = Lock()

    @property
    def dirty(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def edit(self, key: EntryKey, **fields: Any) -> VariantEntryState:
        EntryStateChange(key, tuple(fields.items()))
        with self._lock:
            base = self._base.get(key)
            if base is None:
                entries = self._store.read_entries(self.variant_id, (key,))
                if not entries:
                    raise KeyError(key)
                base = entries[0].state
            values = {**self._pending.get(key, {}), **fields}
            updated = replace(base, **values)
            captured_fields = self._captured_fields.get(key, frozenset())
            pending = {
                name: getattr(updated, name)
                for name in values
                if getattr(updated, name) != getattr(base, name) or name in captured_fields
            }
            self._set_pending(key, base, pending)
            return updated

    def capture(self, operation_id: str | None = None) -> SaveBatch:
        with self._lock:
            if self._captured is not None:
                raise RuntimeError("a save is already in flight")
            batch = SaveBatch(
                self.project_id,
                self.variant_id,
                self._project_revision,
                self._variant_revision,
                tuple(
                    EntryStateChange(key, tuple(values.items()), self._base[key].revision)
                    for key, values in self._pending.items()
                ),
                operation_id,
                uuid4().hex,
            )
            self._captured = batch
            self._captured_fields = {key: frozenset(values) for key, values in self._pending.items()}
            return batch

    def acknowledge(self, receipt: SaveReceipt) -> None:
        with self._lock:
            batch = self._captured
            if batch is None or receipt.capture_token != batch.capture_token:
                raise ValueError("save receipt does not belong to the captured save")
            committed = {entry.entry_key: entry for entry in receipt.entries}
            for change in batch.changes:
                base = committed.get(change.key, self._base[change.key])
                remaining = {
                    name: value for name, value in self._pending[change.key].items() if value != getattr(base, name)
                }
                self._set_pending(change.key, base, remaining)
            self._project_revision = receipt.project_revision
            self._variant_revision = receipt.variant_revision
            self._captured = None
            self._captured_fields.clear()

    def abandon_capture(self) -> None:
        """Retain uncommitted edits after a failed storage operation."""
        with self._lock:
            self._captured = None
            self._captured_fields.clear()
            for key, values in tuple(self._pending.items()):
                base = self._base[key]
                self._set_pending(
                    key, base, {name: value for name, value in values.items() if value != getattr(base, name)}
                )

    def save(self, operation_id: str | None = None) -> SaveReceipt:
        with self._save_lock:
            batch = self.capture(operation_id)
            try:
                receipt = self._store.commit(batch)
            except Exception:
                self.abandon_capture()
                raise
            self.acknowledge(receipt)
            return receipt

    def _set_pending(self, key: EntryKey, base: VariantEntryState, pending: dict[str, Any]) -> None:
        if pending:
            self._base[key] = base
            self._pending[key] = pending
        else:
            self._base.pop(key, None)
            self._pending.pop(key, None)
