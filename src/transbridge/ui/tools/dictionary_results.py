# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read-only dictionary results with wrapping and inline query highlights."""

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from math import ceil

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PyQt6.QtWidgets import QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView, QWidget

_HEADERS = ("原文", "译文", "来源文件")
_HORIZONTAL_MARGIN = 8
_VERTICAL_MARGIN = 4


class _DictionaryResultsModel(QAbstractTableModel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._rows: tuple[tuple[str, str, str], ...] = ()

    def replace_rows(self, rows: tuple[tuple[str, str, str], ...]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self._rows[index.row()][index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return super().headerData(section, orientation, role)


def _wrapped_document(text: str, font: QFont, width: int) -> QTextDocument:
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setPlainText(text)
    document.setTextWidth(max(1, width))
    return document


def _match_spans(text: str, query: str) -> Iterator[tuple[int, int]]:
    """Map matches of the folded query back to the original character ranges."""
    folded = text.casefold()
    start = folded.find(query)
    if start < 0:
        return
    offsets = []
    if len(folded) != len(text):
        offsets = [0]
        for character in text:
            offsets.append(offsets[-1] + len(character.casefold()))
    previous_end = 0
    while start >= 0:
        end = start + len(query)
        original_start = bisect_right(offsets, start) - 1 if offsets else start
        original_end = bisect_left(offsets, end) if offsets else end
        if original_end > previous_end:
            yield max(original_start, previous_end), original_end
            previous_end = original_end
        start = folded.find(query, end)


def _document_length(text: str) -> int:
    # QTextDocument collapses CRLF and addresses positions in UTF-16 code units.
    return len(text.replace("\r\n", "\n").encode("utf-16-le", errors="surrogatepass")) // 2


class _DictionaryResultsDelegate(QStyledItemDelegate):
    query = ""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = index.data()
        styled.text = ""
        widget = styled.widget
        widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, widget)
        area = styled.rect.adjusted(_HORIZONTAL_MARGIN, _VERTICAL_MARGIN, -_HORIZONTAL_MARGIN, -_VERTICAL_MARGIN)
        if not text or area.isEmpty():
            return

        document = _wrapped_document(text, styled.font, area.width())
        context = QAbstractTextDocumentLayout.PaintContext()
        palette = QPalette(styled.palette)
        if styled.state & QStyle.StateFlag.State_Selected:
            palette.setBrush(QPalette.ColorRole.Text, palette.brush(QPalette.ColorRole.HighlightedText))
        context.palette = palette
        if self.query and index.column() < 2:
            context.selections = self._highlight(document, text, styled.palette)
        painter.save()
        painter.setClipRect(area, Qt.ClipOperation.IntersectClip)
        painter.translate(area.topLeft())
        document.documentLayout().draw(painter, context)
        painter.restore()

    def _highlight(
        self, document: QTextDocument, text: str, palette: QPalette
    ) -> list[QAbstractTextDocumentLayout.Selection]:
        match_format = QTextCharFormat()
        light_surface = palette.base().color().lightness() >= palette.text().color().lightness()
        match_format.setBackground(QColor("#f9e9b0" if light_surface else "#5c4c28"))
        match_format.setForeground(palette.text())
        selections = []
        cursor = QTextCursor(document)
        offset, previous_end = 0, 0
        for start, end in _match_spans(text, self.query):
            offset += _document_length(text[previous_end:start])
            cursor.setPosition(offset)
            offset += _document_length(text[start:end])
            cursor.setPosition(offset, QTextCursor.MoveMode.KeepAnchor)
            selection = QAbstractTextDocumentLayout.Selection()
            selection.cursor = cursor
            selection.format = match_format
            selections.append(selection)
            previous_end = end
        return selections


class DictionaryResultsTable(QTableView):
    """Expose the full result set while measuring only rows entering the viewport."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results = _DictionaryResultsModel(self)
        self._delegate = _DictionaryResultsDelegate(self)
        self._sized_rows: set[int] = set()
        self._layout_metrics = None

        self.setModel(self._results)
        self.setItemDelegate(self._delegate)
        self.setProperty("tbComponentKind", "table")
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().hide()
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(1)

    def replace_results(self, rows: tuple[tuple[str, str, str], ...], query: str) -> None:
        self._delegate.query = query.strip().casefold()
        self._sized_rows.clear()
        self._results.replace_rows(rows)
        self.scrollToTop()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        width = self.viewport().width()
        source = min(160, width // 5)
        original = (width - source) // 2
        for column, size in enumerate((original, width - source - original, source)):
            self.setColumnWidth(column, size)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        self._fit_visible_rows()
        super().paintEvent(event)

    def _fit_visible_rows(self) -> None:
        row = self.rowAt(0)
        if row < 0:
            return
        viewport_height = self.viewport().height()
        widths = tuple(self.columnWidth(column) for column in range(len(_HEADERS)))
        font = self.font()
        grid = int(self.showGrid())
        metrics = widths, font, grid
        if metrics != self._layout_metrics:
            self._layout_metrics = metrics
            self._sized_rows.clear()
        count = self._results.rowCount()
        while row < count and self.rowViewportPosition(row) < viewport_height:
            if row not in self._sized_rows:
                height = 0.0
                for column, width in enumerate(widths):
                    text = self._results.index(row, column).data()
                    document = _wrapped_document(text, font, width - 2 * _HORIZONTAL_MARGIN - grid)
                    height = max(height, document.size().height())
                self.setRowHeight(row, ceil(height) + 2 * _VERTICAL_MARGIN + grid)
                self._sized_rows.add(row)
            row += 1
