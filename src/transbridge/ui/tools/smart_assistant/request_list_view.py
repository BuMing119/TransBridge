"""Small request progress projection with explicit user control intents."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QMenu, QPushButton, QVBoxLayout, QWidget

from .request_progress_view import request_status
from .submission_scope import SubmissionScopeView
from .task_presentation import task_summary
from .theme_support import SmartAssistantTheme


class RequestListView(QWidget):
    requests_changed = pyqtSignal(object)
    message_scopes_changed = pyqtSignal(object)
    pending_changed = pyqtSignal(int, int)
    undo_changed = pyqtSignal(str, bool)
    control = pyqtSignal(str, str)
    clarify = pyqtSignal()
    stop_generation = pyqtSignal()
    retry_input = pyqtSignal()
    timeline = pyqtSignal(str)
    undo_round = pyqtSignal()
    locate = pyqtSignal(object)
    review = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tasks = ()
        self.context_waits = {}
        title = QLabel("当前会话的任务")
        layout.addWidget(title)
        self.items = QListWidget()
        self.items.setAccessibleName("任务目标、阶段与结果")
        self.items.setMaximumHeight(110)
        layout.addWidget(self.items)
        self._work_widgets = [title, self.items]
        actions = QHBoxLayout()
        self.controls = {}
        for title, command in (
            ("继续", "resume"),
            ("暂停", "pause"),
            ("取消任务", "cancel"),
            ("重新处理", "restart"),
            ("重试上下文", "retry_context"),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, value=command: self._act(value))
            actions.addWidget(button)
            self._work_widgets.append(button)
            self.controls[command] = button
        self.locate_button = QPushButton("原始要求")
        self.locate_button.clicked.connect(self._locate)
        actions.addWidget(self.locate_button)
        self._work_widgets.append(self.locate_button)
        layout.addLayout(actions)
        history = QHBoxLayout()
        self.result_button = QPushButton("查看结果")
        self.result_button.clicked.connect(self._review)
        history.addWidget(self.result_button)
        self._work_widgets.append(self.result_button)
        self.timeline_request = QPushButton("更多操作")
        self.timeline_request.clicked.connect(self._more)
        history.addWidget(self.timeline_request)
        self._work_widgets.append(self.timeline_request)
        self.timeline_session = QPushButton("会话过程")
        self.timeline_session.clicked.connect(lambda: self.timeline.emit(""))
        history.addWidget(self.timeline_session)
        layout.addLayout(history)
        self.pending = QPushButton("选择补充要求所属的任务")
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
        self.undo_button = QPushButton("撤销上述范围的修改")
        self.undo_button.clicked.connect(self.undo_round.emit)
        layout.addWidget(self.undo_button)
        self.show_undo("", available=False)
        self._requests = []
        self.scopes = {}
        self.scope_view = SubmissionScopeView(SmartAssistantTheme())
        layout.addWidget(self.scope_view)
        self.timeline_request.setEnabled(False)
        self.items.currentRowChanged.connect(lambda row: self.timeline_request.setEnabled(row >= 0))
        self.items.currentRowChanged.connect(self._show_scope)
        self.items.currentRowChanged.connect(self._selection_changed)
        self.display(())

    def set_pending(self, count, routing=0, replanning=0):
        self.pending.setText(f"处理未接纳的要求 ({count})" if replanning else f"选择补充要求所属的任务 ({count})")
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
        selected_request = self._selected()
        selected_id = selected_request.request_id if selected_request is not None else None
        self._requests = list(requests)
        for widget in self._work_widgets:
            widget.setVisible(bool(self._requests))
        self.items.clear()
        for request in self._requests:
            dependency_failures = sum(
                any(reason.startswith("dependency_failed:") for reason in item.waiting_reasons)
                for item in request.items
            )
            detail = f" · {dependency_failures}项未执行：前置事项失败" if dependency_failures else ""
            if request.request_id in self.context_waits:
                detail += "\n上下文准备暂停：" + self.context_waits[request.request_id].get(
                    "message", "请重试上下文准备。"
                )
            self.items.addItem(f"{request.goal}\n{task_summary(request, self._tasks)}{detail}")
        if self._requests:
            selected = next((i for i, r in enumerate(self._requests) if r.request_id == selected_id), 0)
            self.items.setCurrentRow(selected)
        self._selection_changed()
        self.requests_changed.emit(tuple(self._requests))

    def set_tasks(self, tasks):
        self._tasks = tuple(tasks)
        for index, request in enumerate(self._requests):
            text = f"{request.goal}\n{task_summary(request, tasks)}"
            if request.request_id in self.context_waits:
                text += "\n上下文准备暂停：" + self.context_waits[request.request_id].get(
                    "message", "请重试上下文准备。"
                )
            self.items.item(index).setText(text)

    def _selected(self):
        index = self.items.currentRow()
        return self._requests[index] if 0 <= index < len(self._requests) else None

    def _selection_changed(self, *_args):
        request = self._selected()
        opened = request is not None and request.status == "open"
        self.controls["resume"].setVisible(bool(opened and request.pause_reasons))
        self.controls["pause"].setVisible(bool(opened and not request.pause_reasons))
        self.controls["cancel"].setVisible(opened)
        self.controls["restart"].setVisible(
            bool(request and request.status in {"failed", "cancelled"} and not request.unsettled)
        )
        self.controls["retry_context"].setVisible(bool(opened and request.request_id in self.context_waits))
        self.result_button.setVisible(request is not None and request.status not in {"open", "stopping"})

    def _locate(self):
        request = self._selected()
        if request is not None:
            self.locate.emit(request.source_message_ids)

    def _review(self):
        request = self._selected()
        if request is not None:
            self.review.emit(request.request_id, False)

    def _more(self):
        request = self._selected()
        if request is None:
            return
        menu = QMenu(self)
        menu.addAction("查看执行过程", self._show_timeline)
        if request.status == "open":
            menu.addAction("移动未执行事项", lambda: self._act("reassign"))
        if request_status(request) == "执行结果待核对":
            menu.addAction("核对执行结果", lambda: self._act("reconcile"))
        menu.exec(self.timeline_request.mapToGlobal(self.timeline_request.rect().bottomLeft()))

    def set_scopes(self, scopes, message_scopes=None):
        self.scopes = scopes
        self.message_scopes_changed.emit(message_scopes or {})
        self._show_scope(self.items.currentRow())

    def _show_scope(self, index):
        request = self._requests[index] if 0 <= index < len(self._requests) else None
        self.scope_view.set_scope(self.scopes.get(request.request_id) if request is not None else None)

    def _show_timeline(self):
        index = self.items.currentRow()
        if 0 <= index < len(self._requests):
            self.timeline.emit(self._requests[index].request_id)

    def _act(self, command):
        index = self.items.currentRow()
        if 0 <= index < len(self._requests):
            self.control.emit(self._requests[index].request_id, command)
