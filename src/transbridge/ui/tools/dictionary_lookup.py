# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Modeless read-only lookup of saved bilingual examples."""

from __future__ import annotations

import logging
from threading import Event

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from transbridge.application.projects.dictionary_apply import dictionary_library
from transbridge.ui.tools.dictionary_results import DictionaryResultsTable
from transbridge.ui.tools.terminology.async_row_filter import AsyncRowFilter
from transbridge.ui.tools.terminology.row_search import RowMatches, SearchDocument
from transbridge.ui.workers import ApiWorker

_logger = logging.getLogger(__name__)
_Row = tuple[str, str, str]


def _load_rows(library, cancelled: Event) -> tuple[_Row, ...]:
    rows = []
    for row in library.lookup_rows():
        if cancelled.is_set():
            return ()
        rows.append(row)
    return tuple(rows)


class DictionaryLookupDialog(QDialog):
    def __init__(self, ctx, parent=None, *, base_dir=None):
        super().__init__(parent)
        self.setWindowTitle("词典查询")
        self.resize(820, 540)
        self._ctx = ctx
        self._base_dir = base_dir
        self._scope = self._context_scope()
        self._worker: ApiWorker | None = None
        self._cancel = Event()
        self._closed = False
        self._rows: tuple[_Row, ...] = ()
        self._filter = AsyncRowFilter(self)

        layout = QVBoxLayout(self)
        query = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setClearButtonEnabled(True)
        self._search.setPlaceholderText("搜索原文或译文")
        self._search.setAccessibleName(self._search.placeholderText())
        query.addWidget(self._search, 1)
        self._count = QLabel()
        query.addWidget(self._count)
        layout.addLayout(query)
        self._table = DictionaryResultsTable(self)
        layout.addWidget(self._table, 1)
        self._search.textChanged.connect(self._filter.set_query)
        self._filter.matches_changed.connect(self._show_matches)
        self._filter.status_changed.connect(self._search_status)
        self.finished.connect(self._stop)
        for name in ("collection_changed", "project_changed", "project_installed", "variant_changed"):
            if (signal := getattr(ctx, name, None)) is not None:
                signal.connect(self._context_changed)
        if (signal := getattr(ctx, "text_encodings_changed", None)) is not None:
            signal.connect(self.refresh_scope)
        self.refresh_scope()
        self._search.setFocus()

    def focus_query(self, text: str | None = None) -> None:
        if text is not None:
            self._search.setText(text)
        self._search.setFocus()
        self._search.selectAll()

    def _context_scope(self):
        return getattr(self._ctx, "active_project_id", None), getattr(self._ctx, "game_profile", None)

    def _context_changed(self, *_args) -> None:
        scope = self._context_scope()
        if scope != self._scope:
            self._scope = scope
            self.refresh_scope()

    def refresh_scope(self) -> None:
        if self._closed:
            return
        self._cancel.set()
        self._worker = None
        self._rows = ()
        self._filter.set_rows((), _search_document)
        self._count.clear()
        try:
            library = dictionary_library(self._ctx, self._base_dir)
            self._cancel = Event()
            worker = ApiWorker(
                _load_rows,
                library,
                self._cancel,
                route_http_errors=False,
                report_api_status=False,
                cancel_read_on_close=self._cancel.set,
            )
            worker.result.connect(self._loaded)
            worker.finished.connect(self._finished_reading)
            worker.finished.connect(worker.deleteLater)
            self._worker = worker
            worker.start()
        except Exception:
            _logger.exception("无法加载查询词典")
            if self._worker is not None:
                self._worker.deleteLater()
                self._worker = None

    @pyqtSlot(object)
    def _loaded(self, rows: tuple[_Row, ...]) -> None:
        if not self._closed and self.sender() is self._worker:
            self._rows = rows
            self._filter.set_rows(rows, _search_document)

    @pyqtSlot()
    def _finished_reading(self) -> None:
        if self.sender() is self._worker:
            self._worker = None

    def _show_matches(self, matches: RowMatches) -> None:
        query = self._search.text().strip()
        rows = (
            self._rows
            if isinstance(matches.rows, range) and len(matches.rows) == len(self._rows)
            else (tuple(self._rows[i] for i in matches.rows))
        )
        self._table.replace_results(rows, query)
        self._count.setText(f"{len(rows)} 条结果")

    def _search_status(self, pending: bool, error: str) -> None:
        if pending or error:
            self._table.replace_results((), self._search.text().strip())
            self._count.clear()
        if error:
            _logger.warning("词典查询失败：%s", error)

    def _stop(self) -> None:
        self._closed = True
        self._cancel.set()
        self._filter.close()


def _search_document(row: _Row) -> SearchDocument:
    return SearchDocument(row[:2])
