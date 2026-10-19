"""Page navigation for cloud history endpoints that do not expose a total."""

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget


class HistoryPager(QWidget):
    page_requested = pyqtSignal(int)
    PAGE_SIZE = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("请先选择项目", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.previous = QPushButton("上一页", self)
        self.next = QPushButton("下一页", self)
        self.page = QSpinBox(self)
        self.page.setRange(1, 9999)
        self.page.setKeyboardTracking(False)
        self.page.setAccessibleName("历史记录页码")
        row.addWidget(self.previous)
        row.addWidget(QLabel("第", self))
        row.addWidget(self.page)
        row.addWidget(QLabel("页", self))
        row.addWidget(self.next)
        row.addStretch(1)
        layout.addLayout(row)
        self.previous.clicked.connect(lambda: self.page_requested.emit(max(1, self.page.value() - 1)))
        self.next.clicked.connect(lambda: self.page_requested.emit(self.page.value() + 1))
        self.page.valueChanged.connect(self.page_requested.emit)
        self.reset()

    def set_page(self, page: int) -> None:
        with QSignalBlocker(self.page):
            self.page.setValue(page)

    def reset(self, message: str = "请先选择项目") -> None:
        self.set_page(1)
        self.status.setText(message)
        self.status.setToolTip("")
        for widget in (self.previous, self.next, self.page):
            widget.setEnabled(False)

    def loading(self, page: int) -> None:
        self.reset(f"正在加载第 {page} 页…")
        self.set_page(page)

    def show_result(self, page: int, count: int, *, paginated: bool = True) -> None:
        self.set_page(page)
        self.previous.setEnabled(paginated and page > 1)
        self.next.setEnabled(paginated and count >= self.PAGE_SIZE and page < self.page.maximum())
        self.page.setEnabled(paginated)
        if not paginated:
            message = f"指定词条的历史 · {count} 条记录（此查询不分页）"
        elif not count:
            message = "没有匹配的历史记录" if page == 1 else f"第 {page} 页没有更多记录，可返回上一页"
        else:
            message = f"第 {page} 页 · 本页 {count} 条记录"
        self.status.setText(message)
        self.status.setToolTip("")

    def show_error(self, page: int, message: str, *, paginated: bool = True) -> None:
        self.show_result(page, 0, paginated=paginated)
        self.status.setText(f"第 {page} 页加载失败，请点击查询或刷新重试。\n{message}")
        self.status.setToolTip(message)
