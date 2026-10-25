"""Content-first, fixed-height review list without one widget per entry."""

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem


class SyncReviewListDelegate(QStyledItemDelegate):
    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange}:
            watched.verticalHeader().setDefaultSectionSize(watched.fontMetrics().height() * 3 + 28)
        return super().eventFilter(watched, event)

    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        return QSize(220, option.fontMetrics.height() * 3 + 28)

    def paint(self, painter, option, index) -> None:
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
        painter.save()
        # QPalette handles both application themes and high-contrast selection.
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        foreground = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.setPen(option.palette.color(foreground))
        rect = option.rect.adjusted(12, 10, -12, -8)
        height = option.fontMetrics.height()
        title = " ".join((index.siblingAtColumn(2).data() or index.data() or "（空译文）").split())
        original = " ".join((index.data() or "（空原文）").split())
        status = index.siblingAtColumn(4).data()
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignTop,
            option.fontMetrics.elidedText(title, Qt.TextElideMode.ElideRight, rect.width()),
        )
        rect.setTop(rect.top() + height + 4)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignTop,
            option.fontMetrics.elidedText(original, Qt.TextElideMode.ElideRight, rect.width()),
        )
        rect.setTop(rect.top() + height + 4)
        if not selected:
            painter.setPen(option.palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignTop,
            option.fontMetrics.elidedText(status, Qt.TextElideMode.ElideRight, rect.width()),
        )
        painter.restore()
