"""Small request progress projection with explicit user control intents."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from .request_progress_view import request_status


class RequestListView(QWidget):
    requests_changed = pyqtSignal(object)
    pending_changed = pyqtSignal(int, int)
    undo_changed = pyqtSignal(str, bool)
    control = pyqtSignal(str, str)
    clarify = pyqtSignal()
    stop_generation = pyqtSignal()
    retry_input = pyqtSignal()
    timeline = pyqtSignal(str)
    undo_round = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("工作请求")
        layout.addWidget(title)
        self.items = QListWidget()
        self.items.setAccessibleName("用户请求与未完成事项")
        self.items.setMaximumHeight(110)
        layout.addWidget(self.items)
        self._work_widgets = [title, self.items]
        actions = QHBoxLayout()
        for title, command in (
            ("查看/继续", "resume"),
            ("暂停推进", "pause"),
            ("取消请求", "cancel"),
            ("调整归属", "reassign"),
            ("核对结果", "reconcile"),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, value=command: self._act(value))
            actions.addWidget(button)
            self._work_widgets.append(button)
        layout.addLayout(actions)
        history = QHBoxLayout()
        self.timeline_request = QPushButton("查看过程")
        self.timeline_request.clicked.connect(self._show_timeline)
        history.addWidget(self.timeline_request)
        self._work_widgets.append(self.timeline_request)
        self.timeline_session = QPushButton("会话过程")
        self.timeline_session.clicked.connect(lambda: self.timeline.emit(""))
        history.addWidget(self.timeline_session)
        layout.addLayout(history)
        self.pending = QPushButton("澄清待定归属")
        self.pending.clicked.connect(self.clarify.emit)
        layout.addWidget(self.pending)
        self.pending.hide()
        self.retry = QPushButton("重试待处理输入")
        self.retry.clicked.connect(self.retry_input.emit)
        layout.addWidget(self.retry)
        self.retry.hide()
        self.undo_message = QLabel()
        self.undo_message.setWordWrap(True)
        layout.addWidget(self.undo_message)
        self.undo_button = QPushButton("撤销本轮修改")
        self.undo_button.clicked.connect(self.undo_round.emit)
        layout.addWidget(self.undo_button)
        self.show_undo("", available=False)
        self._requests = []
        self.timeline_request.setEnabled(False)
        self.items.currentRowChanged.connect(lambda row: self.timeline_request.setEnabled(row >= 0))
        self.display(())

    def set_pending(self, count, routing=0):
        self.pending.setText(f"澄清待定归属 ({count})")
        self.pending.setVisible(bool(count))
        self.retry.setVisible(bool(routing))
        self.pending_changed.emit(count, routing)

    def show_undo(self, message, *, available):
        self.undo_message.setText(message)
        self.undo_message.setVisible(bool(message))
        self.undo_button.setVisible(available)
        self.undo_button.setEnabled(available)
        self.undo_changed.emit(message, available)

    def display(self, requests):
        selected = self.items.currentRow()
        self._requests = list(requests)
        for widget in self._work_widgets:
            widget.setVisible(bool(self._requests))
        self.items.clear()
        for request in self._requests:
            done = sum(item.status == "satisfied" for item in request.items)
            dependency_failures = sum(
                any(reason.startswith("dependency_failed:") for reason in item.waiting_reasons)
                for item in request.items
            )
            label = request_status(request)
            detail = f" · {dependency_failures}项未执行：前置事项失败" if dependency_failures else ""
            self.items.addItem(f"{request.goal} · {done}/{len(request.items)} · {label}{detail}")
        if self._requests:
            self.items.setCurrentRow(min(max(selected, 0), len(self._requests) - 1))
        self.requests_changed.emit(tuple(self._requests))

    def _show_timeline(self):
        index = self.items.currentRow()
        if 0 <= index < len(self._requests):
            self.timeline.emit(self._requests[index].request_id)

    def _act(self, command):
        index = self.items.currentRow()
        if 0 <= index < len(self._requests):
            self.control.emit(self._requests[index].request_id, command)
