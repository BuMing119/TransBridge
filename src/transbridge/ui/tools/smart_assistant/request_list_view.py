"""Small request progress projection with explicit user control intents."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

_STATUS = {
    "open": "待完成",
    "stopping": "取消中",
    "completed": "已完成",
    "failed": "部分失败/失败",
    "cancelled": "已取消",
    "superseded": "已替换",
}


class RequestListView(QWidget):
    control = pyqtSignal(str, str)
    clarify = pyqtSignal()
    stop_generation = pyqtSignal()
    retry_input = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("用户请求"))
        self.items = QListWidget()
        self.items.setAccessibleName("用户请求与未完成事项")
        self.items.setMaximumHeight(110)
        layout.addWidget(self.items)
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
        layout.addLayout(actions)
        self.pending = QPushButton("澄清待定归属")
        self.pending.clicked.connect(self.clarify.emit)
        layout.addWidget(self.pending)
        self.pending.hide()
        self.retry = QPushButton("重试待处理输入")
        self.retry.clicked.connect(self.retry_input.emit)
        layout.addWidget(self.retry)
        self.retry.hide()
        stop = QPushButton("停止本轮生成")
        stop.clicked.connect(self.stop_generation.emit)
        layout.addWidget(stop)
        self._requests = []

    def set_pending(self, count, routing=0):
        self.pending.setText(f"澄清待定归属 ({count})")
        self.pending.setVisible(bool(count))
        self.retry.setVisible(bool(routing))
        self.setVisible(bool(count or routing or self._requests))

    def display(self, requests):
        selected = self.items.currentRow()
        self._requests = list(requests)
        self.items.clear()
        for request in self._requests:
            done = sum(item.status == "satisfied" for item in request.items)
            waiting = sorted({reason for item in request.items for reason in item.waiting_reasons})
            label = "已暂停推进" if request.pause_reasons else _STATUS[request.status]
            if waiting and request.status == "open":
                label = "待确认" if set(waiting) & {"approval", "approval_revalidation"} else "等待处理"
            if any(e.status == "outcome_unknown" for e in request.effects) or any(
                d.status == "outcome_unknown" for d in request.dispatches
            ):
                label = "待核对执行结果"
            self.items.addItem(f"{request.goal} · {done}/{len(request.items)} · {label}")
        if self._requests:
            self.items.setCurrentRow(min(max(selected, 0), len(self._requests) - 1))
        self.setVisible(bool(self._requests))

    def _act(self, command):
        index = self.items.currentRow()
        if 0 <= index < len(self._requests):
            self.control.emit(self._requests[index].request_id, command)
