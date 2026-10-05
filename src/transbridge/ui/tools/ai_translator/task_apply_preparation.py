"""Detached result preparation and GUI-thread delivery for one atomic apply."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, pyqtSlot


@dataclass(frozen=True)
class PreparedApplication:
    entries: tuple
    baselines: tuple


@dataclass(frozen=True)
class DraftMergeInput:
    # Baselines are the session's private snapshots, never live context entries.
    entries: tuple
    changes: tuple
    source_sizes: tuple

    def prepare(self) -> PreparedApplication:
        changes = dict(self.changes)
        entries = tuple(
            replace(deepcopy(entry), translation=changes[entry.identity][0], stage=changes[entry.identity][1])
            if entry.identity in changes
            else deepcopy(entry)
            for entry in self.entries
        )
        baselines = []
        offset = 0
        for source, size in self.source_sizes:
            # A separate baseline is required to detect later direct field edits.
            baselines.append((source, deepcopy(entries[offset : offset + size])))
            offset += size
        return PreparedApplication(entries, tuple(baselines))


class ApplyDelivery(QObject):
    """Retain the worker until finished; deliver once on the creating GUI thread."""

    def __init__(self, worker, completed):
        super().__init__()
        self.worker = worker
        self.completed = completed
        self.value = None
        self.error = None
        worker.result.connect(self._result)
        worker.error.connect(self._error)
        worker.finished.connect(self._finished)

    @pyqtSlot(object)
    def _result(self, value):
        self.value = value

    @pyqtSlot(str)
    def _error(self, message):
        self.error = message

    @pyqtSlot()
    def _finished(self):
        try:
            self.completed(self.value, self.error)
        finally:
            self.worker.deleteLater()
            self.deleteLater()
