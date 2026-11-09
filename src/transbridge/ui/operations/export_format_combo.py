# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Export format choice with a theme-aware, font-independent dropdown indicator."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox


class ExportFormatComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QComboBox { padding-right: 36px; }"
            "QComboBox::drop-down { width: 30px; }"
            "QComboBox::down-arrow { image: none; }"
        )

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, self
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(QPen(self.palette().color(group, QPalette.ColorRole.Mid), 1))
        painter.drawLine(rect.left(), rect.center().y() - 8, rect.left(), rect.center().y() + 8)
        pen = QPen(self.palette().color(group, QPalette.ColorRole.Text), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = rect.center().x(), rect.center().y()
        painter.drawPolyline(QPolygonF([QPointF(x - 5, y - 2), QPointF(x, y + 3), QPointF(x + 5, y - 2)]))
