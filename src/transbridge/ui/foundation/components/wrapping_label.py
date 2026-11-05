"""Plain-text labels that can break long filenames without hiding their suffix."""

from math import ceil

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAbstractTextDocumentLayout, QPainter, QPalette, QTextDocument, QTextOption
from PyQt6.QtWidgets import QLabel, QSizePolicy, QStyle, QStyleOption, QWidget


class WrappingLabel(QLabel):
    """Preserve QLabel's text API while measuring and painting with the same wrap rule."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        # QLabel's normal word wrap reserves the longest word and clips it when
        # forced narrower. Both sizing and painting must allow breaking that word.
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _document(self, width: int) -> QTextDocument:
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(self.alignment() & Qt.AlignmentFlag.AlignHorizontal_Mask)
        document.setDefaultTextOption(option)
        document.setPlainText(self.text())
        document.setTextWidth(max(1, width))
        return document

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        margins = self.contentsMargins()
        horizontal = margins.left() + margins.right() + 2 * self.margin()
        vertical = margins.top() + margins.bottom() + 2 * self.margin()
        return ceil(self._document(width - horizontal).size().height()) + vertical

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        area = self.contentsRect().adjusted(self.margin(), self.margin(), -self.margin(), -self.margin())
        document = self._document(area.width())
        y = area.top()
        if self.alignment() & Qt.AlignmentFlag.AlignVCenter:
            y += max(0, (area.height() - document.size().height()) / 2)
        elif self.alignment() & Qt.AlignmentFlag.AlignBottom:
            y += max(0, area.height() - document.size().height())
        painter.setClipRect(area)
        painter.translate(area.left(), y)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = self.palette()
        context.palette.setColor(QPalette.ColorRole.Text, self.palette().color(self.foregroundRole()))
        document.documentLayout().draw(painter, context)
        painter.end()
