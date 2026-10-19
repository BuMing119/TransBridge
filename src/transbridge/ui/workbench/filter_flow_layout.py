"""A height-for-width layout for keyboard-accessible filter buttons."""

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem


class FilterFlowLayout(QLayout):
    """Wrap native filter widgets without changing their ownership or tab order."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(3)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt override
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt override
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rectangle: QRect) -> None:  # noqa: N802 - Qt override
        super().setGeometry(rectangle)
        self._arrange(rectangle, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt override
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def _arrange(self, rectangle: QRect, *, apply: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        available = rectangle.adjusted(left, top, -right, -bottom)
        x, y, row_height = available.x(), available.y(), 0
        spacing = max(0, self.spacing())
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().boundedTo(item.maximumSize()).expandedTo(item.minimumSize())
            if row_height and x + size.width() > available.right() + 1:
                x = available.x()
                y += row_height + spacing
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + spacing
            row_height = max(row_height, size.height())
        return y + row_height - rectangle.y() + bottom
