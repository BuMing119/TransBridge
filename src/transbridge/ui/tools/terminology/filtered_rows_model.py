"""Qt row mapping over matches prepared outside the GUI thread."""

from PyQt6.QtCore import QAbstractProxyModel, QModelIndex, Qt, pyqtSignal

from .row_search import RowMatches


class FilteredRowsModel(QAbstractProxyModel):
    """Swap precomputed row positions without per-row Qt filtering callbacks."""

    rows_changing = pyqtSignal()

    def __init__(self, model, parent=None) -> None:
        super().__init__(parent)
        self._matches = RowMatches.all(model.rowCount())
        self.setSourceModel(model)
        model.modelAboutToBeReset.connect(self._source_resetting)
        model.modelReset.connect(self._source_reset)

    def set_matches(self, matches: RowMatches) -> None:
        self.rows_changing.emit()
        self.beginResetModel()
        self._matches = matches
        self.endResetModel()

    def _source_resetting(self) -> None:
        self.beginResetModel()
        self._matches = RowMatches.all(0)

    def _source_reset(self) -> None:
        self._matches = RowMatches.all(self.sourceModel().rowCount())
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._matches.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else self.sourceModel().columnCount()

    def index(self, row, column, parent=QModelIndex()):
        if parent.isValid() or not 0 <= row < self.rowCount() or not 0 <= column < self.columnCount():
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, _index):
        return QModelIndex()

    def mapToSource(self, index):  # noqa: N802 - Qt API
        if not index.isValid() or index.model() is not self or not 0 <= index.row() < self.rowCount():
            return QModelIndex()
        return self.sourceModel().index(self._matches.rows[index.row()], index.column())

    def mapFromSource(self, index):  # noqa: N802 - Qt API
        if not index.isValid() or index.model() is not self.sourceModel():
            return QModelIndex()
        row = self._matches.visible_row(index.row())
        return QModelIndex() if row is None else self.index(row, index.column())

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        return self.sourceModel().data(self.mapToSource(index), role)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802 - Qt API
        return self.sourceModel().headerData(section, orientation, role)

    def flags(self, index):
        return self.sourceModel().flags(self.mapToSource(index))
