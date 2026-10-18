"""Editable row mapping backed by immutable, asynchronously searched snapshots."""

from PyQt6.QtCore import Qt, pyqtSignal

from transbridge.application.terminology_profiles import normalize_term
from transbridge.ui.tools.terminology.async_row_filter import AsyncRowFilter
from transbridge.ui.tools.terminology.filtered_rows_model import FilteredRowsModel
from transbridge.ui.tools.terminology.row_search import SearchDocument


class ProfileMappingFilter(FilteredRowsModel):
    status_changed = pyqtSignal(bool, str)

    def __init__(self, model, parent=None, *, thread_pool=None):
        super().__init__(model, parent)
        self._query = ""
        self._changed_only = False
        # QObject ownership avoids treating a rejected dialog close as final teardown.
        self.search = AsyncRowFilter(self, thread_pool=thread_pool)
        self.search.matches_changed.connect(self.set_matches)
        self.search.status_changed.connect(self.status_changed)
        model.modelReset.connect(self._refresh)
        model.dataChanged.connect(self._source_changed)
        for before, after in (
            (model.rowsAboutToBeInserted, model.rowsInserted),
            (model.rowsAboutToBeRemoved, model.rowsRemoved),
        ):
            before.connect(lambda *_args: self._source_resetting())
            after.connect(self._structure_changed)
        self._refresh()

    def set_matches(self, matches):
        if matches.rows != self._matches.rows:
            super().set_matches(matches)

    def set_query(self, query):
        self._query = query
        self._filter()

    def set_changed_only(self, enabled):
        self._changed_only = enabled
        self._filter(debounce=False)

    def _filter(self, *, debounce=True):
        self.search.set_query(
            normalize_term(self._query), ("changed" if self._changed_only else "",), debounce=debounce
        )

    def _refresh(self):
        rows, published = self.sourceModel().search_snapshot()

        def document(row):
            return SearchDocument(
                tuple(normalize_term(value) for value in row[:3]), ("changed" if row not in published else "",)
            )

        self.search.set_rows(rows, document)

    def _source_changed(self, top, bottom, roles):
        first, last = self.mapFromSource(top), self.mapFromSource(bottom)
        if first.isValid() and last.isValid():
            self.dataChanged.emit(first, last, roles)
        self._refresh()

    def _structure_changed(self, *_args):
        self._source_reset()
        self._refresh()

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802 - Qt API
        return self.sourceModel().setData(self.mapToSource(index), value, role)

    def close(self):
        self.search.close()
