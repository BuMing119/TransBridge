"""Task groups backed by the original, stable ParaTranz page indices."""

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QTabBar, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ElidedLabel

PAGE_GROUPS = (
    ("概览", ((0, "概览"),)),
    ("翻译", ((1, "文件"), (2, "词条"), (3, "术语"), (7, "导出"))),
    ("协作", ((8, "讨论"), (4, "成员"))),
    ("记录", ((5, "历史记录"), (6, "贡献统计"))),
)


class ParaTranzNavigation(QWidget):
    page_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.project_label = ElidedLabel("尚未选择项目", self)
        self.project_label.setTextFormat(Qt.TextFormat.PlainText)
        self.project_label.setAccessibleName("当前浏览的 ParaTranz 项目")
        layout.addWidget(self.project_label)
        self.groups = QTabBar(self)
        self.groups.setAccessibleName("ParaTranz 工作分类")
        self.groups.setExpanding(False)
        for label, _pages in PAGE_GROUPS:
            self.groups.addTab(label)
        layout.addWidget(self.groups)
        secondary = QHBoxLayout()
        self.pages = QTabBar(self)
        self.pages.setAccessibleName("当前分类中的页面")
        self.pages.setExpanding(False)
        policy = self.pages.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.pages.setSizePolicy(policy)
        secondary.addWidget(self.pages)
        secondary.addStretch(1)
        layout.addLayout(secondary)
        self._remembered_pages = {index: pages[0][0] for index, (_label, pages) in enumerate(PAGE_GROUPS)}
        self.groups.currentChanged.connect(self._group_changed)
        self.pages.currentChanged.connect(self._page_changed)
        self.select_page(0)

    def select_page(self, page_index: int) -> None:
        for group_index, (_label, pages) in enumerate(PAGE_GROUPS):
            indices = tuple(index for index, _name in pages)
            if page_index not in indices:
                continue
            self._remembered_pages[group_index] = page_index
            with QSignalBlocker(self.groups), QSignalBlocker(self.pages):
                self.groups.setCurrentIndex(group_index)
                if tuple(self.pages.tabData(index) for index in range(self.pages.count())) != indices:
                    while self.pages.count():
                        self.pages.removeTab(0)
                    for index, name in pages:
                        tab = self.pages.addTab(name)
                        self.pages.setTabData(tab, index)
                self.pages.setCurrentIndex(indices.index(page_index))
                self.pages.setVisible(len(pages) > 1)
            return

    def set_project(self, project: dict | None) -> None:
        name = str(project.get("name") or "未命名项目") if project else "尚未选择项目"
        text = f"当前浏览：{name}" if project else name
        self.project_label.set_full_text(text)
        self.project_label.setToolTip(text)
        self.project_label.setAccessibleDescription(text)
        self.groups.setEnabled(bool(project))
        self.pages.setEnabled(bool(project))

    def _group_changed(self, group_index: int) -> None:
        if group_index in self._remembered_pages:
            page_index = self._remembered_pages[group_index]
            self.select_page(page_index)
            self.page_requested.emit(page_index)

    def _page_changed(self, index: int) -> None:
        if index >= 0:
            page_index = self.pages.tabData(index)
            self._remembered_pages[self.groups.currentIndex()] = page_index
            self.page_requested.emit(page_index)
