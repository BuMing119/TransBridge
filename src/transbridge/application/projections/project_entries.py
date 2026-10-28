"""Reuse the preceding Variant's immutable entry rows while rebuilding its read model."""

from threading import RLock
from typing import Any

from transbridge.application.io.identity import EntryKey
from transbridge.persistence.v2.variant import VariantEntryState, VariantSnapshot

from .models import freeze_projection_value


class ProjectEntryProjection:
    """Keep at most one Variant snapshot and one row per currently present entry."""

    def __init__(self) -> None:
        self._snapshot: VariantSnapshot | None = None
        self._rows: tuple[Any, ...] = ()
        self._frozen_rows: Any = freeze_projection_value(())
        self._by_key: dict[EntryKey, tuple[VariantEntryState, Any]] = {}
        self._lock = RLock()

    def rows(self, snapshot: VariantSnapshot | None) -> tuple[Any, ...]:
        with self._lock:
            if snapshot is self._snapshot:
                return self._rows
            previous = self._by_key if self._snapshot is not None and snapshot is not None else {}
            if snapshot is not None and self._snapshot is not None and snapshot.ref != self._snapshot.ref:
                previous = {}
            current = {}
            rows = []
            for entry in () if snapshot is None else snapshot.entries:
                known = previous.get(entry.entry_key)
                row = known[1] if known is not None and known[0] is entry else freeze_projection_value(entry.to_dict())
                current[entry.entry_key] = entry, row
                rows.append(row)
            self._snapshot, self._by_key, self._rows = snapshot, current, tuple(rows)
            self._frozen_rows = freeze_projection_value(self._rows)
            return self._rows

    def frozen_rows(self, snapshot: VariantSnapshot | None) -> Any:
        """Return the immutable row container without refreezing every row on publication."""
        with self._lock:
            self.rows(snapshot)
            return self._frozen_rows
