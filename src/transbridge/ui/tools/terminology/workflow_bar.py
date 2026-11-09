# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""One compact explanation of the current stage and its next action."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class TerminologyWorkflowBar(QWidget):
    edit_requested = pyqtSignal()
    publish_requested = pyqtSignal()
    build_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._pending = 0
        self._editing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.title = QLabel("正在读取术语状态…", self)
        self.title.setWordWrap(True)
        self.title.setProperty("tbTerminologySectionTitle", True)
        row.addWidget(self.title, 1)
        self.edit_button = QPushButton("继续编辑", self)
        self.edit_button.setProperty("tbTerminologyPrimary", True)
        self.edit_button.clicked.connect(self.edit_requested)
        self.publish_button = QPushButton("检查影响并发布…", self)
        self.publish_button.setProperty("tbTerminologyPrimary", True)
        self.publish_button.clicked.connect(self.publish_requested)
        row.addWidget(self.edit_button)
        row.addWidget(self.publish_button)
        self.build_button = QPushButton("重新提取术语…", self)
        self.build_button.setProperty("tbTerminologyPrimary", True)
        self.build_button.clicked.connect(self.build_requested)
        row.addWidget(self.build_button)
        self.build_button.hide()
        self.edit_button.hide()
        self.publish_button.hide()
        layout.addLayout(row)
        self.detail = QLabel("", self)
        self.detail.setWordWrap(True)
        self.detail.setProperty("tbSecondary", True)
        layout.addWidget(self.detail)

    def render(self, state):
        self._state = state
        self.edit_button.setVisible(state.has_version and not state.has_draft)
        self.publish_button.setVisible(state.can_publish)
        self.build_button.setVisible(state.has_draft and state.next_action == "build")
        self.publish_button.setToolTip("先检查逐条变化和发布条件，再确认发布。")
        self.set_pending(self._pending)

    def set_pending(self, pending):
        self._pending = pending
        if self._state is None:
            return
        title, detail = self._state.display_copy(pending)
        self.title.setText("正在编辑术语" if self._editing and not pending else title)
        self.detail.setText(detail)

    def set_editing(self, editing):
        self._editing = editing
        self.set_pending(self._pending)
