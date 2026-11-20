# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Load the selected ESP record's fields without blocking the editor."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from transbridge.application.dialogue.record_fields import find_record_entries, record_editor_id
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workers import ApiWorker


class RecordBrowser(QObject):
    changed = pyqtSignal()
    entry_requested = pyqtSignal(object)

    def __init__(self, view, workers):
        super().__init__(view)
        self.view = view
        self._workers = workers
        self._running = None
        self.reset()
        view.record_entry_selected.connect(self._select_row)
        view.browse_mode_changed.connect(self._select_mode)

    def reset(self):
        self.preferred = None
        self._wanted = self._selected = None
        self._rows = None
        self._error = ""
        self._collection_version = None
        self.view.set_record_available(False)

    def bind_collection(self, collection):
        self._collection_version = collection, collection.collection_revision

    def refresh_opening_keys(self, collection, keys, preview):
        """Single-field records retain the opening list while removed rows disappear."""
        previous = self._collection_version
        self.bind_collection(collection)
        if previous == self._collection_version:
            return keys
        if not isinstance(collection, ProjectRowsCollection):
            return tuple(key for key in keys if key in collection)
        updates = collection.view_updates_from(previous[1]) if previous and previous[0] is collection else None
        if updates is None:
            return preview.editor_navigation_keys()
        removed = {update.entry_key for update in updates if update.tombstone}
        return tuple(key for key in keys if key not in removed) if removed else keys

    def _select_row(self, row):
        entries = self.view.record_model.entries
        if self.view.browsing_mode == "record" and 0 <= row < len(entries):
            self.entry_requested.emit(entries[row].identity)

    def _select_mode(self, mode):
        self.preferred = mode
        self.changed.emit()

    def present(self, collection, entry, record, scope):
        """Show a record immediately; coalesce fast switches behind one worker."""
        key = (collection, collection.collection_revision, record, scope)
        if key != self._wanted:
            self._wanted = key
            self._rows = None
            self._error = ""
        self._selected = entry
        if self._rows is None and self._running is None and not self._error:
            if isinstance(collection, TranslationEntryCollection) and len(collection) <= WORKBENCH_PAGE_SIZE:
                self._rows = find_record_entries(collection, entry)
            else:
                self._start()
        rows = self._rows or (entry,)
        row = next((i for i, item in enumerate(rows) if item.identity == entry.identity), None)
        if row is None:
            rows, row = (entry,), 0
        self.view.set_browse_mode("record")
        self.view.show_record(
            rows, row, record_type=record.signature, editor_id=record_editor_id(entry), form_id=record.form_id
        )
        self.view.show_message(self._error or ("正在读取记录字段…" if self._rows is None else ""))
        return tuple(item.identity for item in rows)

    @property
    def pending(self):
        return self._rows is None and not self._error

    def _start(self):
        key = self._wanted
        worker = ApiWorker(find_record_entries, key[0], self._selected, route_http_errors=False)
        self._running = key
        worker.result.connect(self._loaded)
        worker.error.connect(self._failed)
        worker.finished.connect(self._finished)
        workers = self._workers
        worker.finished.connect(lambda: workers.remove(worker))
        worker.finished.connect(worker.deleteLater)
        workers.append(worker)
        worker.start()

    @pyqtSlot(object)
    def _loaded(self, rows):
        if self._wanted == self._running:
            self._rows = rows

    @pyqtSlot(str)
    def _failed(self, message):
        if self._wanted == self._running:
            self._error = f"读取 ESP 记录字段失败：{message}。重新打开词条可重试。"

    @pyqtSlot()
    def _finished(self):
        self._running = None
        if self._wanted is not None:
            self.changed.emit()
