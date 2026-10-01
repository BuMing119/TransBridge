"""Responsive conversation/sidebar split without touching execution state."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter


class AssistantColumns(QSplitter):
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self._narrow = None
        self._restore_sidebar = False
        self.setHandleWidth(1)
        self.setChildrenCollapsible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.count():
            return
        narrow = event.size().width() < 680
        if narrow == self._narrow:
            return
        sidebar = self.widget(0)
        if narrow:
            self._restore_sidebar = not sidebar._collapsed
            sidebar.set_collapsed(True)
        elif self._restore_sidebar:
            sidebar.set_collapsed(False)
        self._narrow = narrow
        side_width = 48 if sidebar._collapsed else 200
        self.setSizes([side_width, max(1, event.size().width() - side_width - self.handleWidth())])
