# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Elide the current label while keeping native combo box behavior."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class ElidedComboBox(QComboBox):
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        label_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        option.currentText = option.fontMetrics.elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, max(0, label_rect.width() - 4)
        )
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
