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

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        if self.count() >= 2:
            # The sidebar folds below the breakpoint. Advertising its expanded
            # width makes QDockWidget retain a minimum that prevents that resize.
            compact_width = self.widget(0).COMPACT_WIDTH
            size.setWidth(compact_width + self.widget(1).minimumSizeHint().width() + self.handleWidth())
        return size

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
        side_width = sidebar.COMPACT_WIDTH if sidebar._collapsed else 200
        self.setSizes([side_width, max(1, event.size().width() - side_width - self.handleWidth())])
