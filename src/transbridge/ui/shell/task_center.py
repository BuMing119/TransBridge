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
from transbridge.application.tasks import (
    OwnerRef,
    TaskCenterAction,
    TaskCenterActions,
    TaskCenterItem,
    TaskHistoryNavigationRegistry,
    TaskRecoveryIntentRegistry,
    TaskRetryContext,
    TaskRetryIntentRegistry,
    TaskRuntime,
)
from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState
from transbridge.ui.presentation.task_projection import TaskProjectionBinding


class TaskCenterPanel(QWidget):
    pause_requested = pyqtSignal(str, int)
    resume_requested = pyqtSignal(str, int)
    cancel_requested = pyqtSignal(str, int)
    action_requested = pyqtSignal(object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        configure_accessible_widget(self, name="任务活动中心", description="查看任务状态和可用控制操作")
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
        for task_list in (self._current, self._history, self._recovery):
            ComponentStyle.apply_static(task_list, ComponentKind.TABLE)
        self._tabs.addTab(self._current, "当前")
        self._tabs.addTab(self._history, "历史")
        self._tabs.addTab(self._recovery, "恢复")
        root.addWidget(self._tabs)
        self._reason = QLabel("选择任务可查看当前允许的操作。")
        self._reason.setWordWrap(True)
        self._reason.setAccessibleName("任务操作与恢复说明")
        self._reason.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        ComponentStyle.apply_static(self._reason, ComponentKind.NOTIFICATION)
        ComponentStyle.apply_state(self._reason, SemanticState.INFO)
        root.addWidget(self._reason)
        actions = QHBoxLayout()
        self._pause = QPushButton("暂停")
        self._resume = QPushButton("继续")
        self._cancel = QPushButton("停止")
        self._recover = QPushButton("恢复")
        self._retry = QPushButton("重试")
        self._open_result = QPushButton("打开结果")
        self._open_log = QPushButton("打开日志")
        self._pause.setAccessibleName("暂停所选任务")
        self._resume.setAccessibleName("继续所选任务")
        self._cancel.setAccessibleName("停止所选任务")
        self._recover.setAccessibleName("恢复所选任务")
        self._retry.setAccessibleName("重试所选任务")
        self._open_result.setAccessibleName("打开所选任务结果")
        self._open_log.setAccessibleName("打开所选任务日志")
        for button in self._action_buttons():
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            button.setEnabled(False)
            actions.addWidget(button)
        ComponentStyle.apply_state(self._cancel, SemanticState.WARNING)
        root.addLayout(actions)
        self._current.currentItemChanged.connect(lambda *_: self._update_actions())
        self._history.currentItemChanged.connect(lambda *_: self._update_actions())
        self._recovery.currentItemChanged.connect(lambda *_: self._update_actions())
        self._tabs.currentChanged.connect(lambda *_: self._update_actions())
        self._pause.clicked.connect(lambda: self._emit_selected(self.pause_requested))
        self._resume.clicked.connect(lambda: self._emit_selected(self.resume_requested))
        self._cancel.clicked.connect(lambda: self._emit_selected(self.cancel_requested))
        self._recover.clicked.connect(lambda: self._emit_action(TaskCenterAction.RECOVER))
        self._retry.clicked.connect(lambda: self._emit_action(TaskCenterAction.RETRY))
        self._open_result.clicked.connect(lambda: self._emit_action(TaskCenterAction.OPEN_RESULT))
        self._open_log.clicked.connect(lambda: self._emit_action(TaskCenterAction.OPEN_LOG))

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
            item = QListWidgetItem(f"{record.display_name}  ·  {record.state_label}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(f"Run ID: {record.run_id}")
            self._history.addItem(item)
        self._update_actions()

    def render_recovery(self, records) -> None:
        self._recovery.clear()
        for record in records:
            item = QListWidgetItem(f"{record.display_name}  ·  {record.state_label}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(record.reason_message)
            self._recovery.addItem(item)
        self._update_actions()

    def show_error(self, message: str) -> None:
        self._reason.setText(message)
        update_accessible_state(self._reason, message)
        self._reason.setProperty("tbStatusId", "error")
        ComponentStyle.apply_state(self._reason, SemanticState.ERROR)

    def _update_actions(self) -> None:
        selected_list = self._tabs.currentWidget()
        item = selected_list.currentItem()
        if item is None:
            state = None
        elif selected_list is self._current:
            state = self._states.get(item.data(Qt.ItemDataRole.UserRole))
        else:
            state = item.data(Qt.ItemDataRole.UserRole)
        available = None if state is None else state.available_actions
        current_selected = selected_list is self._current
        self._pause.setEnabled(bool(current_selected and available and available.pause))
        self._resume.setEnabled(bool(current_selected and available and available.resume))
        self._cancel.setEnabled(bool(current_selected and available and (available.stop or available.cancel)))
        self._recover.setEnabled(bool(available and available.recover))
        self._retry.setEnabled(bool(available and available.retry))
        self._open_result.setEnabled(bool(available and available.open_result))
        self._open_log.setEnabled(bool(available and available.open_log))
        if state is None:
            explanation = "选择任务可查看当前允许的操作。"
            self._reason.setText(explanation)
            update_accessible_state(self._reason, explanation)
            for button in self._action_buttons():
                button.setToolTip("")
                button.setAccessibleDescription(explanation)
        elif isinstance(state, TaskCenterItem):
            explanation = state.reason_message or f"当前对象：{state.display_name}。仅显示当前真实可用的操作。"
            self._reason.setText(explanation)
            update_accessible_state(self._reason, explanation)
            self._reason.setProperty("tbStatusId", state.source)
            ComponentStyle.apply_state(self._reason, SemanticState.INFO)
        else:
            title = state.display_context.title
            capability = (
                "此任务不支持暂停/恢复。"
                if not available.pause and not available.resume
                else "仅显示任务真实声明的控制能力。"
            )
            recovery = "停止只请求取消；已完成结果会保留，是否可恢复取决于任务声明的能力。"
            explanation = f"当前对象：{title}（Run ID {state.run_id}）。{capability}{recovery}"
            self._reason.setText(explanation)
            update_accessible_state(self._reason, explanation)
            self._reason.setProperty("tbStatusId", state.state.value)
            ComponentStyle.apply_state(self._reason, SemanticState.INFO)
            self._cancel.setToolTip(f"请求停止“{title}”。{recovery}")
            self._cancel.setAccessibleDescription(f"当前对象：{title}。{recovery}")
            self._pause.setAccessibleDescription(f"暂停当前对象：{title}")
            self._resume.setAccessibleDescription(f"继续当前对象：{title}")

    def _emit_action(self, action: TaskCenterAction) -> None:
        selected_list = self._tabs.currentWidget()
        item = selected_list.currentItem()
        if item is None:
            return
        state = (
            self._states.get(item.data(Qt.ItemDataRole.UserRole))
            if selected_list is self._current
            else item.data(Qt.ItemDataRole.UserRole)
        )
        if state is not None:
            self.action_requested.emit(state, action)

    def _action_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self._pause,
            self._resume,
            self._cancel,
            self._recover,
            self._retry,
            self._open_result,
            self._open_log,
        )

    def _emit_selected(self, signal) -> None:
        if self._tabs.currentWidget() is not self._current:
            return
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
    navigation_requested = pyqtSignal(object)

    def __init__(self, runtime, runtime_context, panel: TaskCenterPanel, *, parent=None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._context = runtime_context
        self._panel = panel
        metadata = dict(runtime_context.metadata)
        self._actor = OwnerRef(
            owner_id=runtime_context.owner_id,
            entrypoint=metadata.get("entrypoint", "gui"),
            project_id=getattr(runtime_context, "project_id", None),
            variant_id=getattr(runtime_context, "variant_id", None),
            session_id=getattr(runtime_context, "session_id", None),
            permissions=frozenset((*runtime_context.permissions, TaskRuntime.MANAGE_PERMISSION)),
        )
        context_ref = metadata.get("context_ref", "")
        context_fingerprint = metadata.get("context_fingerprint", "")
        self._retry_context = (
            TaskRetryContext(self._actor, context_ref, context_fingerprint)
            if context_ref and context_fingerprint
            else None
        )
        self._actions = self._resolve_actions(runtime.use_cases)
        self._binding = TaskProjectionBinding(runtime.tasks, self._actor, self.activity_changed.emit)
        self.activity_changed.connect(panel.render_activity)
        panel.pause_requested.connect(lambda run_id, revision: self._control("pause", run_id, revision))
        panel.resume_requested.connect(lambda run_id, revision: self._control("resume", run_id, revision))
        panel.cancel_requested.connect(lambda run_id, revision: self._control("cancel", run_id, revision))
        panel.action_requested.connect(self._execute_action)

    def start(self) -> None:
        self._binding.start()
        self.refresh_catalogs()

    def refresh_catalogs(self) -> None:
        try:
            self._panel.render_history(
                self._actions.list_history(self._actor, retry_context=self._retry_context, limit=100)
            )
            self._panel.render_recovery(self._actions.list_recovery(self._actor))
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

    def _execute_action(self, selected, action: TaskCenterAction) -> None:
        if not isinstance(selected, TaskCenterItem):
            self._panel.show_error("当前活动尚未提供可验证的历史或恢复记录。")
            return
        error_message = ""
        try:
            result = self._actions.execute(
                selected,
                action,
                self._actor,
                retry_context=self._retry_context,
            )
            if result.navigation is not None:
                self.navigation_requested.emit(result.navigation)
        except Exception as exc:
            error_message = f"任务操作失败：{exc}"
        finally:
            self.refresh_catalogs()
        if error_message:
            self._panel.show_error(error_message)

    @staticmethod
    def _resolve_actions(use_cases) -> TaskCenterActions:
        if "task_center_actions" in use_cases.names():
            return use_cases.resolve("task_center_actions")
        return TaskCenterActions(
            use_cases.resolve("task_history"),
            use_cases.resolve("task_recovery"),
            TaskRetryIntentRegistry(),
            TaskRecoveryIntentRegistry(),
            TaskHistoryNavigationRegistry(),
        )


__all__ = ["TaskCenterController", "TaskCenterPanel"]
