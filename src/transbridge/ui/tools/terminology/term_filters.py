"""Explicit, full-collection filtering controls shared by terminology tables."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QWidget

from transbridge.application.terminology.browse_queries import TermBrowseStatus, TermFilter
from transbridge.application.terminology.models import ScopeKind


class TermFilterBar(QWidget):
    changed = pyqtSignal(object)

    def __init__(self, parent=None, *, draft=False):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索全部原名或译名")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索全部术语")
        self.scope = QComboBox(self)
        self.scope.setAccessibleName("术语范围筛选")
        for text, value in (("全部范围", None), ("整个项目", ScopeKind.PROJECT), ("指定插件", ScopeKind.PLUGIN)):
            self.scope.addItem(text, value)
        self.status = QComboBox(self)
        self.status.setAccessibleName("草稿状态筛选")
        for text, value in (
            ("全部状态", None),
            ("已确认", TermBrowseStatus.EFFECTIVE),
            ("待复核", TermBrowseStatus.REVIEW_REQUIRED),
            ("待决定", TermBrowseStatus.UNRESOLVED),
            ("已停用", TermBrowseStatus.EXCLUDED),
        ):
            self.status.addItem(text, value)
        self.status.setVisible(draft)
        self.apply_button = QPushButton("应用筛选", self)
        for widget in (self.search, self.scope, self.status):
            widget.setProperty("tbComponentKind", "input")
        layout.addWidget(self.search, 1)
        layout.addWidget(self.scope)
        layout.addWidget(self.status)
        layout.addWidget(self.apply_button)
        self.apply_button.clicked.connect(self.apply)
        self.search.returnPressed.connect(self.apply)
        self.scope.currentIndexChanged.connect(self.apply)
        self.status.currentIndexChanged.connect(self.apply)

    def apply(self, *_args):
        self.changed.emit(TermFilter(self.search.text().strip(), self.scope.currentData(), self.status.currentData()))

    def show_review_required(self):
        self.status.setCurrentIndex(self.status.findData(TermBrowseStatus.REVIEW_REQUIRED))
        self.apply()
