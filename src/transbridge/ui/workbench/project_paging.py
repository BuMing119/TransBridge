# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Display a quick first page and retain the complete active Workbench source."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Event

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from transbridge.converter.context_categories import context_category
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workbench.filters_presenter import FilterState
from transbridge.ui.workbench.resident_rows import (
    ResidentTableRows,
    load_resident_rows,
    patch_resident_rows,
    query_resident_rows,
)
from transbridge.ui.workbench.workflow_presenter import StatisticsSummary
from transbridge.ui.workers import ApiWorker


@dataclass(frozen=True)
class ScopeStatistics:
    categories: Counter
    stages: Counter
    labels: Counter
    labeled: int
    summary: StatisticsSummary


_DEFAULT_QUERY = FilterState(), (None, False)


class WorkbenchProjectPaging(QObject):
    statistics_ready = pyqtSignal()

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.collection = None
        self.worker = None
        self.sort = (None, False)
        self.result = None
        self._result_key = None
        self._resident = None
        self._resident_worker = None
        self._resident_revision = None
        self._resident_cancel = Event()
        self._query_cancel = Event()
        self._requested_query = None
        self._terminology_window = None
        self._outcome = None
        self.statistics = None
        self._statistics_key = None
        self._statistics_worker = None
        self._callbacks = []
        self._closed = Event()
        self._rendering = False
        self._selected_query = None
        self._viewport_timer = QTimer(self)
        self._viewport_timer.setSingleShot(True)
        self._viewport_timer.timeout.connect(self._load_viewport)
        bar = host._table.verticalScrollBar()
        bar.valueChanged.connect(self._queue_viewport)
        bar.rangeChanged.connect(self._queue_viewport)
        host._table.scope_sort_requested.connect(self._sort_requested)
        host._table.scope_select_all_requested.connect(self._select_all)
        host._table.selectionModel().selectionChanged.connect(self._selection_changed)
        host._ctx.dirty_changed.connect(self._queue_resident)
        host._ctx.dirty_changed.connect(self._queue_statistics)

    def refresh(self, collection) -> bool:
        collection = collection if isinstance(collection, ProjectRowsCollection) else None
        if self.collection is not collection:
            self._resident_cancel.set()
            self._resident_cancel = Event()
            self._query_cancel.set()
            self.collection = collection
            self._resident = self._resident_revision = None
            self.result = self._result_key = None
            self._requested_query = self._selected_query = self._terminology_window = None
            self.statistics = self._statistics_key = None
            self._callbacks.clear()
            if collection is not None:
                self._display()
        if collection is None:
            return False
        revision = collection.collection_revision
        if self._resident is not None and revision != self._resident_revision:
            self._resident = patch_resident_rows(self._resident, collection.view_updates_from(self._resident_revision))
            self._resident_revision = revision
            if self._resident is None:
                self.result = self._result_key = None
        self.request_query()
        self._queue_statistics()
        return True

    def request_query(self) -> None:
        if self.collection is None or self._closed.is_set():
            return
        key = self._query_key()
        if key != self._requested_query:
            self._query_cancel.set()
            self._query_cancel = Event()
            self._requested_query = key
        if not self.resident_ready:
            if key[2:] == _DEFAULT_QUERY:
                self._display()
            self._queue_resident()
            return
        if key == self._result_key:
            self._display()
            return
        if key[2:] == _DEFAULT_QUERY:
            self._install_query(key, self._resident)
            return
        if self.worker is None:
            source, cancelled = self._resident, self._query_cancel
            self._run(
                lambda: query_resident_rows(source, key[2], key[3], cancelled),
                lambda result: self._install_query(key, result),
                query_key=key,
            )

    @property
    def resident_ready(self):
        return (
            self.collection is not None
            and self._resident is not None
            and self._resident_revision == self.collection.collection_revision
        )

    def _queue_resident(self):
        if self.collection is not None and not self.resident_ready and not self._closed.is_set():
            QTimer.singleShot(0, self._load_resident)

    def _load_resident(self):
        collection = self.collection
        if collection is None or self.resident_ready or self._closed.is_set() or self._resident_worker is not None:
            return
        revision, token, cancelled = collection.collection_revision, collection.token, self._resident_cancel
        rows = collection.query_rows()
        results, errors = [], []
        worker = ApiWorker(lambda: load_resident_rows(rows, cancelled), route_http_errors=False)
        self._resident_worker = worker

        def finished():
            self._resident_worker = None
            worker.deleteLater()
            if self._closed.is_set():
                return
            if (
                cancelled.is_set()
                or self.collection is not collection
                or collection.collection_revision != revision
                or collection.token != token
            ):
                self._queue_resident()
            elif results and results[0] is not None:
                self._resident, self._resident_revision = results[0], revision
                self.request_query()
            elif errors:
                self._error(errors[0])

        worker.result.connect(results.append)
        worker.error.connect(errors.append)
        worker.finished.connect(finished)
        worker.start()

    def when_ready(self, callback) -> bool:
        if self.collection is None or self._result_key == self._query_key():
            return True
        self._callbacks.append((self._query_key(), callback))
        self.request_query()
        return False

    def filtered_entries(self):
        return self._completed_query().entries

    def marked_entries(self):
        return self._completed_query().marked

    def selected_entry_ids(self):
        if self.collection is not None and self._selected_query == self._query_key() == self._result_key:
            return self.result.identities
        return None

    def selected_row_entry_ids(self):
        table = self.host._table
        if self.collection is not None and self._result_key == self._query_key():
            identities = self.result.identities
            return tuple(key for start, end in table.selected_row_spans() for key in identities[start : end + 1])
        return table.selected_entry_ids()

    def selection_needs_query(self):
        if self.collection is None or self._result_key == self._query_key():
            return False
        table = self.host._table
        return sum(end - start + 1 for start, end in table.selected_row_spans()) > len(table.selected_entry_ids())

    def selection_entries(self):
        if self.collection is not None and self._result_key == self._query_key():
            return self.result.entries
        return self.host._entries

    def selection_labels(self):
        if self.collection is not None and self._result_key == self._query_key():
            return self.result.labels
        return self.host._entry_labels

    def open_entry(self, key):
        if self.when_ready(lambda: self.open_entry(key)):
            self.host.entry_edit_requested.emit(key)

    def locate(self, target, *, select=False):
        if not self.when_ready(lambda: self.locate(target, select=select)):
            return
        if self.result is None:
            return
        positions = self.result.rows.positions if isinstance(target, str) else self.result.rows.key_positions
        row = positions.get(target)
        if row is not None:
            self.host._table.focus_row(row, select=select)

    def _select_all(self):
        if self.collection is None or not self.when_ready(self._select_all):
            return
        self._selected_query = self._query_key()
        self.host._update_workflow_actions()

    def _selection_changed(self, *_):
        if not self._rendering:
            self._selected_query = None
            if self.selection_needs_query():
                self.request_query()

    def _completed_query(self):
        if self._result_key != self._query_key():
            raise RuntimeError("请等待完整的工程筛选范围准备完成。")
        return self.result

    def _query_key(self):
        return (
            self.collection,
            self.collection.collection_revision,
            self.host._filters_view.state(),
            self.sort,
        )

    def _sort_requested(self, column, descending):
        self.sort = column, descending
        if self.collection is not None:
            self.request_query()

    def _queue_viewport(self, *_args):
        if (
            self.collection is not None
            and not self._closed.is_set()
            and (not self.resident_ready or self.host._terminology_preview.profile_label is not None)
        ):
            self._viewport_timer.start(0)

    def _load_viewport(self):
        if self.worker is not None or self.collection is None or self._closed.is_set():
            return
        collection = self.collection
        first, last = self.host._table.viewport_rows()
        if last < first:
            return
        start = first // WORKBENCH_PAGE_SIZE * WORKBENCH_PAGE_SIZE
        requested = tuple(range(start, last + 1, WORKBENCH_PAGE_SIZE))
        if self.resident_ready:
            if requested != self._terminology_window and self.host._terminology_preview.profile_label is not None:
                self._terminology_window = requested
                visible = (self.result or self._resident).entries
                stop = requested[-1] + WORKBENCH_PAGE_SIZE
                self.host._terminology_preview.set_entries(visible[start:stop], viewport_only=True)
                self.refresh_terminology_projection()
            return
        offsets = collection.missing_pages(requested)
        if not offsets:
            return
        token = collection.token

        def accept(rows):
            if (
                collection is self.collection
                and not self.resident_ready
                and collection.install_pages(rows, token, requested)
            ):
                self._display()

        self._run(lambda: tuple((offset, collection.read_page(offset, token)) for offset in offsets), accept)

    def _install_query(self, key, result):
        if self.collection is None or key != self._query_key() or self._closed.is_set():
            return
        self.result, self._result_key = result, key
        self._display()
        callbacks, self._callbacks = self._callbacks, []
        for requested, callback in callbacks:
            if requested == key:
                callback()

    def table_range(self):
        if self.result is not None:
            scope = (self.collection,) if self._result_key[2:] == _DEFAULT_QUERY else self._result_key
            return len(self.result.entries), self.result.rows, scope
        if self.resident_ready:
            return len(self._resident.entries), self._resident.rows, (self.collection,)
        return len(self.collection), self.collection.logical_rows(), (self.collection,)

    def _display(self):
        if self.collection is None:
            return
        host = self.host
        total, rows, _scope = self.table_range()
        if not isinstance(rows, ResidentTableRows):
            labels = self.collection.labels()
            entries = tuple(rows.values())
        else:
            labels = (self.result or self._resident).labels
            first, last = host._table.viewport_rows()
            if host._table.render_session.content_scope != _scope:
                first, last = 0, WORKBENCH_PAGE_SIZE - 1
            start = first // WORKBENCH_PAGE_SIZE * WORKBENCH_PAGE_SIZE
            self._terminology_window = tuple(range(start, last + 1, WORKBENCH_PAGE_SIZE))
            stop = (max(first, last) // WORKBENCH_PAGE_SIZE + 1) * WORKBENCH_PAGE_SIZE
            entries = rows.entries[start:stop]
        host._entries = list(entries)
        host._entry_labels = labels
        host._filters_view.set_content_visible(True)
        self._rendering = True
        try:
            host._terminology_preview.set_entries(host._entries, viewport_only=True)
            host._populate_table()
        finally:
            self._rendering = False
        host._filtered_total = total
        host._update_count_label()
        host._update_workflow_actions()
        self._queue_viewport()

    def refresh_terminology_projection(self):
        host = self.host
        preview = host._terminology_preview
        host._terminology_status.setText(preview.message)
        host._terminology_status.setVisible(bool(preview.message))
        host._table.set_terminology_projection(
            preview.projection, profile_label=preview.profile_label, pending=preview.pending, error=preview.error
        )
        host._table.viewport().update()

    def _request_statistics(self):
        collection = self.collection
        if collection is None or self._closed.is_set() or self._statistics_worker is not None:
            return
        key = collection, collection.collection_revision, collection.token
        if key == self._statistics_key:
            return
        worker = ApiWorker(lambda: _statistics(collection.statistics()), route_http_errors=False)
        self._statistics_worker = worker

        def received(value):
            if (self.collection, collection.collection_revision, collection.token) == key and not self._closed.is_set():
                self.statistics = value
                self._statistics_key = key
                self.host._summary = value.summary
                self.host._summary_view.set_summary(value.summary)
                self.host._build_category_tags()
                self.host._build_stage_tags()
                self.host._build_label_tags()
                self.host._update_count_label()
                self.host._update_workflow_actions()
                self.statistics_ready.emit()

        def finished():
            self._statistics_worker = None
            worker.deleteLater()
            current = self.collection
            if current is not None and (current, current.collection_revision, current.token) != key:
                QTimer.singleShot(0, self._request_statistics)

        worker.result.connect(received)
        worker.error.connect(self._error)
        worker.finished.connect(finished)
        worker.start()

    def _queue_statistics(self):
        if self.collection is not None and not self._closed.is_set():
            QTimer.singleShot(0, self._request_statistics)

    def current_stage_counts(self, collection):
        if (collection, collection.collection_revision, collection.token) == self._statistics_key:
            return self.statistics.stages
        return None

    def close(self):
        self._closed.set()
        self._viewport_timer.stop()
        self._resident_cancel.set()
        self._query_cancel.set()
        self.collection = None
        self._resident = self.result = None
        self._callbacks.clear()

    def _run(self, operation, accept, *, query_key=None):
        worker = ApiWorker(operation, route_http_errors=False)
        self.worker = worker
        self._outcome = None
        worker.result.connect(self._received)
        worker.error.connect(self._error)
        worker.finished.connect(lambda: self._finished(worker, accept, query_key))
        worker.start()

    def _received(self, value):
        self._outcome = value

    def _error(self, error):
        if self._closed.is_set():
            return
        self.host._progress.setFormat(str(error))
        self.host._progress.show()

    def _finished(self, worker, accept, query_key):
        outcome, self._outcome, self.worker = self._outcome, None, None
        worker.deleteLater()
        if outcome is not None and not self._closed.is_set():
            accept(outcome)
            self._queue_viewport()
        if self.collection is not None:
            key = self._query_key()
            force = bool(self._callbacks) or self.selection_needs_query()
            stale = query_key is not None and query_key != key
            if (key[2:] != _DEFAULT_QUERY or force) and key != self._result_key and (outcome is not None or stale):
                QTimer.singleShot(0, self.request_query)


def _statistics(facts):
    categories, stages, labels = Counter(), Counter(), Counter()
    labeled = 0
    for (context, stage, _translated, membership), count in facts.items():
        categories[context_category(context)] += count
        stages[stage] += count
        if membership:
            labeled += count
            for label in membership:
                labels[label] += count
    summary = StatisticsSummary(
        sum(stages.values()), stages[0], stages[2], sum(stages[stage] for stage in (1, 3, 5, 9)), stages[-1]
    )
    return ScopeStatistics(categories, stages, labels, labeled, summary)
