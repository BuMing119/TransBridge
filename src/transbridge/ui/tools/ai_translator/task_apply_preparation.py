"""Detached result preparation and GUI-thread delivery for one atomic apply."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, pyqtSlot

from transbridge.converter.translation_entry_collection import TranslationEntryCollection


@dataclass(frozen=True)
class PreparedApplication:
    entries: tuple
    baselines: tuple
    collections: tuple = ()


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
        baselines, collections = [], []
        offset = 0
        for source, size in self.source_sizes:
            # A separate baseline is required to detect later direct field edits.
            baselines.append((source, deepcopy(entries[offset : offset + size])))
            collections.append((source, TranslationEntryCollection(entries[offset : offset + size])))
            offset += size
        return PreparedApplication(entries, tuple(baselines), tuple(collections))


def prepare_committed_baseline(capture, expected_baselines):
    """Accept only our committed values; never absorb edits during sliced capture.

    replace_entry_states may increment the immutable entry revision. All other
    projected fields, including nested source metadata, must equal our prepared
    payload. A final GUI exact comparison still guards edits after each slice.
    """
    # These are the separately copied baselines, never entries installed into
    # legacy live collections during the commit.
    expected = {entry.identity: entry for _source, entries in expected_baselines for entry in entries}
    captured = tuple(entry for state in capture.states.values() for entry in state.entries)
    if len(captured) != len(expected):
        raise RuntimeError("应用结果后处理来源已变化，结果已应用但无法继续此任务。")
    for entry in captured:
        before = expected.get(entry.identity)
        actual_values = dict(vars(entry))
        actual_values.pop("revision", None)
        expected_values = {} if before is None else dict(vars(before))
        expected_values.pop("revision", None)
        if before is None or type(entry) is not type(before) or actual_values != expected_values:
            raise RuntimeError("应用结果后条目已被再次修改，结果已应用但无法继续此任务。")
    return capture.prepare()


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
