"""Unified, event-driven task activity surface for the desktop shell."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.ui.presentation.task_projection import TaskProjectionBinding


class TaskCenterPanel(QWidget):
    pause_requested = pyqtSignal(str, int)
    resume_requested = pyqtSignal(str, int)
    cancel_requested = pyqtSignal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("任务活动中心")
        self._states = {}
        self._initial_focus_set = False
        root = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.setAccessibleName("任务分类")
        self._current = QListWidget()
        self._current.setAccessibleName("当前任务")
        self._history = QListWidget()
        self._history.setAccessibleName("任务历史")
        self._recovery = QListWidget()
        self._recovery.setAccessibleName("可恢复任务")
        self._tabs.addTab(self._current, "当前")
        self._tabs.addTab(self._history, "历史")
        self._tabs.addTab(self._recovery, "恢复")
        root.addWidget(self._tabs)
        self._reason = QLabel("选择任务可查看当前允许的操作。")
        self._reason.setWordWrap(True)
        self._reason.setAccessibleName("任务操作与恢复说明")
        self._reason.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        root.addWidget(self._reason)
        actions = QHBoxLayout()
        self._pause = QPushButton("暂停")
        self._resume = QPushButton("继续")
        self._cancel = QPushButton("停止")
        self._pause.setAccessibleName("暂停所选任务")
        self._resume.setAccessibleName("继续所选任务")
        self._cancel.setAccessibleName("停止所选任务")
        for button in (self._pause, self._resume, self._cancel):
            button.setEnabled(False)
            actions.addWidget(button)
        root.addLayout(actions)
        self._current.currentItemChanged.connect(lambda *_: self._update_actions())
        self._pause.clicked.connect(lambda: self._emit_selected(self.pause_requested))
        self._resume.clicked.connect(lambda: self._emit_selected(self.resume_requested))
        self._cancel.clicked.connect(lambda: self._emit_selected(self.cancel_requested))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._initial_focus_set:
            return
        self._initial_focus_set = True
        self._tabs.setFocus(Qt.FocusReason.OtherFocusReason)

    def render_activity(self, state) -> None:
        self._states[state.run_id] = state
        item = self._find(self._current, state.run_id)
        if item is None:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, state.run_id)
            self._current.addItem(item)
        item.setText(f"{state.display_context.title}  ·  {state.state.value}")
        item.setToolTip(f"Run ID: {state.run_id}")
        self._update_actions()

    def render_history(self, records) -> None:
        self._history.clear()
        for record in records:
            item = QListWidgetItem(f"{record.display_name}  ·  {record.state.value}")
            item.setToolTip(f"Run ID: {record.run_id}")
            self._history.addItem(item)

    def render_recovery(self, records) -> None:
        self._recovery.clear()
        for record in records:
            status = "可恢复" if record.recoverable else record.reason_message or record.reason_code
            item = QListWidgetItem(f"{record.display_name}  ·  {status}")
            item.setToolTip(record.reason_message or record.reason_code)
            self._recovery.addItem(item)

    def show_error(self, message: str) -> None:
        self._reason.setText(message)

    def _update_actions(self) -> None:
        item = self._current.currentItem()
        state = None if item is None else self._states.get(item.data(Qt.ItemDataRole.UserRole))
        available = None if state is None else state.available_actions
        self._pause.setEnabled(bool(available and available.pause))
        self._resume.setEnabled(bool(available and available.resume))
        self._cancel.setEnabled(bool(available and (available.stop or available.cancel)))
        if state is not None:
            title = state.display_context.title
            capability = (
                "此任务不支持暂停/恢复。"
                if not available.pause and not available.resume
                else "仅显示任务真实声明的控制能力。"
            )
            recovery = "停止只请求取消；已完成结果会保留，是否可恢复取决于任务声明的能力。"
            explanation = f"当前对象：{title}（Run ID {state.run_id}）。{capability}{recovery}"
            self._reason.setText(explanation)
            self._cancel.setToolTip(f"请求停止“{title}”。{recovery}")
            self._cancel.setAccessibleDescription(f"当前对象：{title}。{recovery}")
            self._pause.setAccessibleDescription(f"暂停当前对象：{title}")
            self._resume.setAccessibleDescription(f"继续当前对象：{title}")

    def _emit_selected(self, signal) -> None:
        item = self._current.currentItem()
        if item is None:
            return
        state = self._states.get(item.data(Qt.ItemDataRole.UserRole))
        if state is not None:
            signal.emit(state.run_id, state.revision)

    @staticmethod
    def _find(widget: QListWidget, run_id: str):
        for index in range(widget.count()):
            item = widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == run_id:
                return item
        return None


class TaskCenterController(QObject):
    """Own one TaskRuntime subscription and marshal events onto the GUI thread."""

    activity_changed = pyqtSignal(object)

    def __init__(self, runtime, runtime_context, panel: TaskCenterPanel, *, parent=None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._context = runtime_context
        self._panel = panel
        metadata = dict(runtime_context.metadata)
        self._actor = OwnerRef(
            owner_id=runtime_context.owner_id,
            entrypoint=metadata.get("entrypoint", "gui"),
            permissions=frozenset((*runtime_context.permissions, TaskRuntime.MANAGE_PERMISSION)),
        )
        self._binding = TaskProjectionBinding(runtime.tasks, self._actor, self.activity_changed.emit)
        self.activity_changed.connect(panel.render_activity)
        panel.pause_requested.connect(lambda run_id, revision: self._control("pause", run_id, revision))
        panel.resume_requested.connect(lambda run_id, revision: self._control("resume", run_id, revision))
        panel.cancel_requested.connect(lambda run_id, revision: self._control("cancel", run_id, revision))

    def start(self) -> None:
        self._binding.start()
        self.refresh_catalogs()

    def refresh_catalogs(self) -> None:
        use_cases = self._runtime.use_cases
        try:
            self._panel.render_history(use_cases.resolve("task_history").list(self._actor, limit=100))
            self._panel.render_recovery(use_cases.resolve("task_recovery").list(self._actor))
        except Exception as exc:
            self._panel.show_error(f"任务目录读取失败：{exc}")

    def close(self) -> None:
        self._binding.close()

    def _control(self, action: str, run_id: str, revision: int) -> None:
        states = {state.run_id: state for state in self._binding.states()}
        state = states.get(run_id)
        if state is None:
            self._panel.show_error("任务已不在当前活动目录中。")
            return
        ref = JobRef(state.job_id, state.owner.owner_id, state.run_id)
        try:
            getattr(self._runtime.tasks, action)(ref, self._actor, expected_revision=revision)
        except Exception as exc:
            self._panel.show_error(f"任务操作失败：{exc}")


__all__ = ["TaskCenterController", "TaskCenterPanel"]
