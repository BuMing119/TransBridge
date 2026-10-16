"""A bounded growing editor that keeps longer requests readable before sending."""

from math import ceil

from PyQt6.QtCore import QSizeF, Qt, pyqtSlot
from PyQt6.QtWidgets import QSizePolicy, QTextEdit


class ComposerEditor(QTextEdit):
    MINIMUM_HEIGHT = 44
    MAXIMUM_HEIGHT = 164

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resizing = False
        self._preferred_height = self.MINIMUM_HEIGHT
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(self.MINIMUM_HEIGHT)
        self.setMaximumHeight(self.MINIMUM_HEIGHT)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.document().documentLayout().documentSizeChanged.connect(self._document_resized)

    @pyqtSlot(QSizeF)
    def _document_resized(self, _size):
        self._fit_content()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_content()

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self._preferred_height)
        return size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setHeight(self.MINIMUM_HEIGHT)
        return size

    def _fit_content(self):
        if self._resizing:
            return
        self._resizing = True
        try:
            chrome = self.height() - self.viewport().height()
            height = ceil(self.document().size().height()) + max(0, chrome)
            target = min(self.MAXIMUM_HEIGHT, max(self.MINIMUM_HEIGHT, height))
            if target != self._preferred_height:
                self._preferred_height = target
                self.setMaximumHeight(target)
                self.updateGeometry()
        finally:
            self._resizing = False
